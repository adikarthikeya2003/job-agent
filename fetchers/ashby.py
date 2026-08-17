"""Tier 1 — Ashby job board JSON API.

Endpoint pattern (public, stable, no auth, documented by Ashby itself):
  GET https://api.ashbyhq.com/posting-api/job-board/{board_token}?includeCompensation=true

`board_token` is the company's slug on Ashby (visible at jobs.ashbyhq.com/{board_token}).
Covers YC-backed startups that have moved off Greenhouse/Lever onto Ashby (Notion, Ramp,
Deel, Vanta, Patreon, Substack, etc.).
"""
from .base import FetchResult, http_get

API = "https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"


def _location(raw: dict) -> str:
    loc = raw.get("location") or ""
    secondary = raw.get("secondaryLocations") or []
    extra = [s.get("location", "") for s in secondary if s.get("location")]
    return "; ".join([loc] + extra) if extra else loc


def fetch(company_cfg: dict) -> FetchResult:
    token = company_cfg["board_token"]
    url = API.format(token=token)
    try:
        resp = http_get(url)
    except Exception as e:
        return FetchResult(company=company_cfg["name"], tier_used=1, source=url,
                            ok=False, error=str(e))
    if resp.status_code != 200:
        return FetchResult(company=company_cfg["name"], tier_used=1, source=url,
                            ok=False, error=f"HTTP {resp.status_code}")
    data = resp.json()
    jobs = []
    for raw in data.get("jobs", []):
        if not raw.get("isListed", True):
            continue
        jobs.append({
            "company": company_cfg["display_name"],
            "title": raw.get("title", ""),
            "location": _location(raw),
            "description": raw.get("descriptionHtml") or raw.get("descriptionPlain") or "",
            "apply_link": raw.get("jobUrl") or raw.get("applyUrl", ""),
            "posted_date": raw.get("publishedAt", ""),
            "external_id": str(raw.get("id", "")),
            "source_tier": 1,
        })
    return FetchResult(company=company_cfg["name"], tier_used=1, source=url, ok=True, jobs=jobs)
