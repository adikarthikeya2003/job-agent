"""Tier 1 — Lever job postings JSON API.

Endpoint pattern (public, stable, no auth, documented by Lever):
  GET https://api.lever.co/v0/postings/{board_token}?mode=json

`board_token` is the company's slug on Lever (visible at jobs.lever.co/{board_token}).
"""
from datetime import datetime, timezone

from .base import FetchResult, http_get

API = "https://api.lever.co/v0/postings/{token}?mode=json"


def iso_posted_date(created_at) -> str:
    """Lever reports `createdAt` as epoch MILLISECONDS, not a date string.

    Passing it through raw put values like "1785533737281" in the posted_date column,
    which sorts and displays as garbage. Normalize to ISO YYYY-MM-DD like every other
    adapter; anything unparseable becomes "" (rendered as "unknown") rather than a
    fabricated date.
    """
    try:
        ms = int(created_at)
    except (TypeError, ValueError):
        return ""
    if ms <= 0:
        return ""
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return ""


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
    for raw in data:
        categories = raw.get("categories", {}) or {}
        description = raw.get("descriptionPlain") or raw.get("description") or ""
        jobs.append({
            "company": company_cfg["display_name"],
            "title": raw.get("text", ""),
            "location": categories.get("location", ""),
            "description": description,
            "apply_link": raw.get("hostedUrl", ""),
            "posted_date": iso_posted_date(raw.get("createdAt")),
            "external_id": str(raw.get("id", "")),
            "source_tier": 1,
        })
    return FetchResult(company=company_cfg["name"], tier_used=1, source=url, ok=True, jobs=jobs)
