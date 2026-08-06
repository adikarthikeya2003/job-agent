#!/usr/bin/env python3
"""Convert Chrome-harvested Google job batches into the browser_inbox drop file.

The Chrome harvest (see scripts/google_harvest.md) saves one JSON array per results
page into ~/Downloads/gharvest_*.json. This merges them, dedupes by job id, stamps
`harvested_at` with the CURRENT time, and writes data/browser_inbox/google.json.

The timestamp is what browser_inbox enforces freshness against, so this script also
deletes the consumed batch files — a leftover batch from an earlier session must never
be silently folded into a later harvest and passed off as fresh.

Usage:  python3 scripts/build_browser_inbox.py [company_name]
"""
import glob
import json
import os
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
INBOX = BASE / "data" / "browser_inbox"
DOWNLOADS = Path.home() / "Downloads"
JOB_URL = "https://www.google.com/about/careers/applications/jobs/results/"


def main() -> int:
    company = sys.argv[1] if len(sys.argv) > 1 else "google"
    batches = sorted(glob.glob(str(DOWNLOADS / "gharvest_*.json")))
    if not batches:
        print(f"No gharvest_*.json batches found in {DOWNLOADS} — run the Chrome harvest first.")
        return 1

    jobs, seen = [], set()
    for path in batches:
        try:
            records = json.loads(Path(path).read_text())
        except (ValueError, OSError) as e:
            print(f"  skipping unreadable batch {os.path.basename(path)}: {e}")
            continue
        for rec in records:
            job_id = str(rec.get("id", "") or "")
            if not job_id or job_id in seen:
                continue
            seen.add(job_id)
            jobs.append({
                "external_id": job_id,
                "title": rec.get("t", ""),
                "location": rec.get("l", ""),
                "description": rec.get("d", ""),
                "apply_link": JOB_URL + rec.get("slug", ""),
                # Google's results cards carry no posting date; left blank rather than
                # invented. Renders as "unknown" downstream.
                "posted_date": "",
            })
        print(f"  merged {os.path.basename(path)} ({len(records)} records)")

    INBOX.mkdir(parents=True, exist_ok=True)
    out = INBOX / f"{company}.json"
    out.write_text(json.dumps({
        "company": company,
        "harvested_at": datetime.now().isoformat(timespec="seconds"),
        "jobs": jobs,
    }, indent=1))
    print(f"\nWrote {len(jobs)} unique jobs -> {out}")

    for path in batches:            # consume, so stale batches can't be reused later
        os.remove(path)
    print(f"Cleared {len(batches)} consumed batch file(s) from {DOWNLOADS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
