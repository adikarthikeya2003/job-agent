"""Persistent SQLite store for seen postings, dedup, and score history.

A job is uniquely identified by (company, external_id). On each run:
  - new postings are inserted and returned as "new"
  - previously seen postings are skipped from output UNLESS resume.md's content hash
    has changed since it was last scored, in which case it's re-scored and re-surfaced
    if it now clears the threshold (or dropped if it no longer does).
"""
import hashlib
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    company TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT,
    location TEXT,
    description TEXT,
    apply_link TEXT,
    posted_date TEXT,
    source_tier INTEGER,
    role_family TEXT,
    seniority TEXT,
    remote_status TEXT,
    sponsorship_flag TEXT,
    match_score REAL,
    top_keywords TEXT,
    resume_hash TEXT,
    first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (company, external_id)
);

CREATE TABLE IF NOT EXISTS run_log (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
    company TEXT,
    tier_used INTEGER,
    ok INTEGER,
    error TEXT,
    jobs_found INTEGER,
    jobs_new INTEGER
);
"""


def resume_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class DedupStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def is_new_or_stale(self, company: str, external_id: str, current_resume_hash: str) -> bool:
        row = self.conn.execute(
            "SELECT resume_hash FROM jobs WHERE company=? AND external_id=?",
            (company, external_id),
        ).fetchone()
        if row is None:
            return True
        return row[0] != current_resume_hash

    def upsert(self, job: dict, resume_hash_val: str):
        # description is only ever read back in-memory during the same run (for
        # scoring); nothing downstream re-reads it from the DB. Truncate to a
        # sanity-check excerpt rather than storing full JD text forever -- at
        # dozens of companies x thousands of postings, full text is what blew
        # jobs.db past GitHub's 100MB push limit within two runs.
        job = {**job, "description": (job.get("description") or "")[:500]}
        self.conn.execute("""
            INSERT INTO jobs (company, external_id, title, location, description, apply_link,
                posted_date, source_tier, role_family, seniority, remote_status,
                sponsorship_flag, match_score, top_keywords, resume_hash, last_seen_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, CURRENT_TIMESTAMP)
            ON CONFLICT(company, external_id) DO UPDATE SET
                title=excluded.title, location=excluded.location, description=excluded.description,
                apply_link=excluded.apply_link, posted_date=excluded.posted_date,
                source_tier=excluded.source_tier, role_family=excluded.role_family,
                seniority=excluded.seniority, remote_status=excluded.remote_status,
                sponsorship_flag=excluded.sponsorship_flag, match_score=excluded.match_score,
                top_keywords=excluded.top_keywords, resume_hash=excluded.resume_hash,
                last_seen_at=CURRENT_TIMESTAMP
        """, (
            job["company"], job["external_id"], job.get("title", ""), job.get("location", ""),
            job.get("description", ""), job.get("apply_link", ""), job.get("posted_date", ""),
            job.get("source_tier", 0), job.get("role_family", ""), job.get("seniority", ""),
            job.get("remote_status", ""), job.get("sponsorship_flag", ""),
            job.get("match_score", 0.0), job.get("top_keywords", ""), resume_hash_val,
        ))
        self.conn.commit()

    def log_run(self, company: str, tier_used: int, ok: bool, error: str,
                jobs_found: int, jobs_new: int):
        self.conn.execute("""
            INSERT INTO run_log (company, tier_used, ok, error, jobs_found, jobs_new)
            VALUES (?,?,?,?,?,?)
        """, (company, tier_used, int(ok), error, jobs_found, jobs_new))
        self.conn.commit()

    def prune_stale(self, days: int = 45) -> int:
        """Drop jobs not re-seen in `days` days (almost certainly closed postings)
        and old run_log rows, then VACUUM to actually shrink the file on disk --
        without this, jobs.db grows unbounded (~1 daily-cron run adds full JD text
        for every posting at every configured company) and eventually exceeds
        GitHub's 100MB hard push limit, breaking the cloud routine."""
        cur = self.conn.execute(
            "DELETE FROM jobs WHERE last_seen_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        deleted = cur.rowcount
        self.conn.execute(
            "DELETE FROM run_log WHERE started_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        self.conn.commit()
        self.conn.execute("VACUUM")
        return deleted

    def close(self):
        self.conn.close()
