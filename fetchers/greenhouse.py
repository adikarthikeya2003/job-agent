"""Tier 1 — Greenhouse job board JSON API.

Endpoint pattern (public, stable, no auth, documented by Greenhouse itself):
  GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true

`board_token` is the company's slug on Greenhouse (visible in their job board URL,
e.g. job-boards.greenhouse.io/{board_token}). This is the lowest-risk, most stable
fetch method in this tool — it's the same API Greenhouse's own embeddable widgets use.
"""
from .base import FetchResult, http_get

API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


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
        jobs.append({
            "company": company_cfg["display_name"],
            "title": raw.get("title", ""),
            "location": (raw.get("location") or {}).get("name", ""),
            "description": raw.get("content", "") or "",
            "apply_link": raw.get("absolute_url", ""),
            "posted_date": raw.get("updated_at", ""),
            "external_id": str(raw.get("id", "")),
            "source_tier": 1,
        })
    return FetchResult(company=company_cfg["name"], tier_used=1, source=url, ok=True, jobs=jobs)
