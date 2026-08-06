"""Orchestrates: fetch (tier-gated) -> tag -> dedup -> score -> log -> output.

Daily automated run: only Tiers 1-3 (config.yaml daily_run.max_tier), enforced here
regardless of what an individual company's config says, so a misconfigured company
entry can never accidentally pull Tier 4/5 into cron/launchd.
"""
import logging
from datetime import datetime
from pathlib import Path

import yaml

from dedup_store import DedupStore, resume_hash
from fetchers import (greenhouse, lever, rss, workday, google_careers, html_scraper,
                       jobright_stub, oracle_hcm, github_md, amazon_jobs,
                       microsoft_careers, ibm_careers, browser_inbox, detail)
from match_scorer import MatchScorer
from resume_parser import parse_resume_sections, sync_resume_md, full_resume_text
from tagger import tag_job, tag_experience_required
from text_clean import clean_description
import output as output_mod

BASE_DIR = Path(__file__).parent
ADAPTERS = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "rss": rss.fetch,
    "workday": workday.fetch,
    "oracle_hcm": oracle_hcm.fetch,
    "google_careers": google_careers.fetch,
    "html": html_scraper.fetch,
    "jobright": jobright_stub.fetch,
    "github_md": github_md.fetch,
    "amazon_jobs": amazon_jobs.fetch,
    "microsoft_careers": microsoft_careers.fetch,
    "ibm_careers": ibm_careers.fetch,
    "browser_inbox": browser_inbox.fetch,   # Tier 5, reads a Chrome-harvested drop file
    # "browser_manual" deliberately excluded — Tier 5, manual CLI path only (cli.py fetch-manual)
}


def load_config(config_path: Path) -> dict:
    return yaml.safe_load(config_path.read_text())


def setup_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"run_{stamp}.log"
    logger = logging.getLogger("job_agent")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fh = logging.FileHandler(log_file)
    sh = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh.setFormatter(fmt)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def _enrich_experience(jobs: list, cfg: dict, logger) -> None:
    """Fill in `experience_required` for surfaced postings whose source published no JD.

    Mutates the job dicts in place (the same objects are referenced by the HTML page's
    list, so both outputs pick this up). Only postings still reading "not stated" are
    fetched, and only from sources with a known detail endpoint — see fetchers/detail.py
    for why this is scoped to the surfaced set rather than the whole board.
    """
    by_name = {c["name"]: c for c in cfg["companies"]}
    pending = [j for j in jobs
               if j.get("experience_required", "not stated") == "not stated"
               and detail.supports(by_name.get(j.get("_cfg_name"), {}).get("adapter", ""))]
    if not pending:
        return

    filled = 0
    for job in pending:
        company_cfg = by_name[job["_cfg_name"]]
        jd = detail.fetch_description(job, company_cfg)
        if not jd:
            continue
        exp = tag_experience_required(job.get("title", ""), jd)
        if exp != "not stated":
            job["experience_required"] = exp
            filled += 1
    logger.info(f"Experience detail pass: {filled}/{len(pending)} surfaced postings "
                f"resolved from per-job JD fetch")


def run(config_path: Path = BASE_DIR / "config.yaml", daily_mode: bool = True,
        company_filter: str | None = None, include_seen: bool = False) -> dict:
    cfg = load_config(config_path)
    logger = setup_logging(BASE_DIR / "logs")

    resume_md_path = (BASE_DIR / cfg["resume"]["source_md"]).resolve()
    resume_tex_path = (BASE_DIR / cfg["resume"]["source_tex"]).resolve()
    if resume_tex_path.exists():
        sync_resume_md(resume_tex_path, resume_md_path)
        logger.info(f"Synced resume.md from {resume_tex_path}")

    sections = parse_resume_sections(resume_md_path)
    full_text = full_resume_text(sections)
    current_hash = resume_hash(full_text)

    scorer = MatchScorer(
        method=cfg["scoring"]["method"],
        embeddings_model=cfg["scoring"]["embeddings_model"],
        section_weights=cfg["scoring"]["section_weights"],
        score_boost=cfg["scoring"].get("score_boost", 1.0),
    )
    threshold = cfg["scoring"]["surface_threshold"]
    us_only = cfg.get("filters", {}).get("us_only", True)
    max_tier = cfg["daily_run"]["max_tier"] if daily_mode else 5

    store = DedupStore(BASE_DIR / "data" / "jobs.db")

    all_jobs, score_distribution = [], []
    all_us_scored = []  # every US posting this run (any score) — feeds the clickable HTML page
    non_us_dropped = 0
    for company_cfg in cfg["companies"]:
        if company_filter and company_cfg["name"] != company_filter:
            continue
        tier = company_cfg["tier"]
        adapter_name = company_cfg["adapter"]

        if tier > max_tier:
            logger.info(f"[SKIP] {company_cfg['name']}: tier {tier} exceeds max_tier "
                        f"{max_tier} for this run — manual-only (use cli.py fetch-manual).")
            store.log_run(company_cfg["name"], tier, False, "tier exceeds max_tier for run", 0, 0)
            continue

        adapter = ADAPTERS.get(adapter_name)
        if adapter is None:
            logger.warning(f"[SKIP] {company_cfg['name']}: no adapter '{adapter_name}'")
            store.log_run(company_cfg["name"], tier, False, f"unknown adapter {adapter_name}", 0, 0)
            continue

        result = adapter(company_cfg)
        if not result.ok:
            level = logger.info if result.manual_only else logger.warning
            level(f"[{'MANUAL-ONLY' if result.manual_only else 'FAIL'}] "
                  f"{company_cfg['name']} (tier {result.tier_used}): {result.error}")
            store.log_run(company_cfg["name"], result.tier_used, False, result.error, 0, 0)
            continue

        logger.info(f"[OK] {company_cfg['name']} (tier {result.tier_used}): "
                    f"{len(result.jobs)} postings fetched")

        for job in result.jobs:
            job["description"] = clean_description(job.get("description", ""))
            tag_job(job, cfg["roles"], cfg["sponsorship"])
            # Remembered so the post-scoring detail pass knows which endpoint to call.
            job["_cfg_name"] = company_cfg["name"]

        # Score the whole company batch together so TF-IDF's IDF weighting is computed
        # across many postings, not a meaningless single resume-vs-one-JD pair.
        jd_texts = [f"{job['title']}\n{job['description']}" for job in result.jobs]
        score_results = scorer.score_batch(sections, jd_texts) if jd_texts else []

        new_count = 0
        for job, score_result in zip(result.jobs, score_results):
            is_new = store.is_new_or_stale(job["company"], job["external_id"], current_hash)

            job["match_score"] = score_result["total"]
            job["top_keywords_list"] = score_result["top_keywords"]
            job["top_keywords"] = ", ".join(score_result["top_keywords"])
            score_distribution.append(job["match_score"])

            store.upsert(job, current_hash)
            if is_new:
                new_count += 1
            if not us_only or job["is_us"]:
                all_us_scored.append(job)
            if (is_new or include_seen) and job["match_score"] >= threshold:
                if not us_only or job["is_us"]:
                    all_jobs.append(job)
                else:
                    non_us_dropped += 1

        store.log_run(company_cfg["name"], result.tier_used, True, "", len(result.jobs), new_count)

    _enrich_experience(all_jobs, cfg, logger)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    written = output_mod.write_outputs(all_jobs, BASE_DIR / "output", stamp)
    html_written = output_mod.write_html(all_us_scored, BASE_DIR / "output", stamp)
    logger.info(f"Clickable page: {html_written['html']} ({html_written['count']} jobs)")

    if score_distribution:
        dist_summary = (f"n={len(score_distribution)} "
                         f"min={min(score_distribution):.1f} "
                         f"max={max(score_distribution):.1f} "
                         f"avg={sum(score_distribution)/len(score_distribution):.1f}")
    else:
        dist_summary = "n=0 (no postings scored this run)"
    logger.info(f"Score distribution: {dist_summary}")
    if us_only:
        logger.info(f"US-only filter active: dropped {non_us_dropped} non-US postings "
                    f"that cleared the score threshold")
    logger.info(f"Surfaced {written['count']} jobs >= threshold {threshold} -> {written['csv']}")

    store.close()
    top_jobs = sorted(all_us_scored, key=lambda j: j["match_score"], reverse=True)[:15]
    return {"surfaced": written["count"], "outputs": written, "score_distribution": dist_summary,
            "top_jobs": top_jobs, "html_page": html_written["html"],
            "html_count": html_written["count"]}
