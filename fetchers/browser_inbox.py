"""Tier 5 — browser-harvested inbox adapter.

For employers whose careers site has no reachable JSON API (Google's runs on
`batchexecute`, Google's internal RPC protocol), the postings are harvested by
driving a real Chrome session and scraping the rendered page, then dropped here as
JSON. This adapter reads that drop file and feeds it into the normal
tag -> dedup -> score -> output pipeline, so browser-sourced jobs are ranked and
filtered exactly like API-sourced ones.

  data/browser_inbox/{company_name}.json
  {
    "company": "google",
    "harvested_at": "2026-08-02T12:44:01",
    "jobs": [{"title", "location", "description", "apply_link",
              "posted_date", "external_id"}, ...]
  }

FRESHNESS IS ENFORCED, NOT ASSUMED. A drop file older than `max_age_minutes`
(default 90) is REFUSED with an explicit error rather than served — the standing
requirement is that a fetch returns currently-open roles, and silently replaying
yesterday's harvest is exactly the failure this guards against. A stale or missing
file surfaces as a visible FAIL in the run log, which is the signal to re-run the
Chrome harvest.

Tier 5 keeps this out of the unattended daily cron (pipeline.py enforces
daily_run.max_tier), because a browser harvest needs Chrome open and an operator
driving it. It runs on demand via `cli.py fetch`.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

from .base import FetchResult

INBOX_DIR = Path(__file__).parent.parent / "data" / "browser_inbox"
DEFAULT_MAX_AGE_MIN = 90


def inbox_path(company_name: str) -> Path:
    return INBOX_DIR / f"{company_name}.json"


def fetch(company_cfg: dict) -> FetchResult:
    name = company_cfg["name"]
    path = inbox_path(name)
    source = str(path)
    max_age = int(company_cfg.get("browser_inbox", {}).get(
        "max_age_minutes", DEFAULT_MAX_AGE_MIN))

    if not path.exists():
        return FetchResult(company=name, tier_used=5, source=source, ok=False,
                           manual_only=True,
                           error=f"no browser harvest at {path.name} — run the Chrome "
                                 f"harvest for '{name}' before fetching")
    try:
        payload = json.loads(path.read_text())
    except (ValueError, OSError) as e:
        return FetchResult(company=name, tier_used=5, source=source, ok=False,
                           error=f"unreadable harvest file: {e}")

    stamp = payload.get("harvested_at", "")
    try:
        harvested = datetime.fromisoformat(stamp)
    except ValueError:
        return FetchResult(company=name, tier_used=5, source=source, ok=False,
                           error=f"harvest file has no usable harvested_at timestamp "
                                 f"({stamp!r}) — refusing to serve possibly-stale jobs")

    age = datetime.now() - harvested
    if age > timedelta(minutes=max_age):
        mins = int(age.total_seconds() // 60)
        return FetchResult(company=name, tier_used=5, source=source, ok=False,
                           manual_only=True,
                           error=f"harvest is {mins} min old (max {max_age}) — re-run the "
                                 f"Chrome harvest; refusing to serve stale postings")

    jobs = []
    for raw in payload.get("jobs", []):
        ext_id = str(raw.get("external_id", "") or "")
        if not ext_id:
            continue
        jobs.append({
            "company": company_cfg["display_name"],
            "title": raw.get("title", ""),
            "location": raw.get("location", ""),
            "description": raw.get("description", "") or "",
            "apply_link": raw.get("apply_link", ""),
            "posted_date": raw.get("posted_date", "") or "",
            "external_id": ext_id,
            "source_tier": 5,
        })

    return FetchResult(company=name, tier_used=5, source=source, ok=True, jobs=jobs)
