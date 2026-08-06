"""Tier 3 — Microsoft careers (Eightfold PCSX) adapter.

Microsoft retired the old `gcsservices.careers.microsoft.com/search/api/v1/search`
endpoint; that host now fails TLS/404s. The current site (apply.careers.microsoft.com)
runs on Eightfold and calls:

  GET https://apply.careers.microsoft.com/api/pcsx/search
      ?domain=microsoft.com&query={q}&start={n}&num={n}

Discovered by instrumenting the live page in a browser and reading back the request
it makes. Two things matter about the `domain` parameter: it is REQUIRED (omitting it
returns HTTP 422 "Missing data for required field"), and it must be exactly
`microsoft.com` — `careers.microsoft.com` and `apply.careers.microsoft.com` both 404.

AUTHENTICATION: this endpoint works unauthenticated, which is what makes it safe for
the daily cron. Note that browsing apply.careers.microsoft.com while logged in returns
*personalized* results ranked against the résumé on file; this adapter deliberately
uses the anonymous ranking instead, so results are reproducible and no session
credential is ever needed or stored.

The list response carries no JD body, so match scores here are title/location/
department based — same limitation as the Workday adapter.
"""
from datetime import datetime

from .base import FetchResult, http_get, robots_allows

SEARCH_API = "https://apply.careers.microsoft.com/api/pcsx/search"
CAREERS_PAGE = "https://apply.careers.microsoft.com/careers"
DOMAIN = "microsoft.com"
PAGE_SIZE = 20
MAX_PAGES = 25

DEFAULT_QUERIES = [
    "data engineer",
    "data scientist",
    "data analyst",
    "machine learning engineer",
    "business intelligence",
]


def _iso(ts) -> str:
    """postedTs arrives as a unix timestamp."""
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def fetch(company_cfg: dict) -> FetchResult:
    if not robots_allows(CAREERS_PAGE):
        return FetchResult(company=company_cfg["name"], tier_used=3, source=SEARCH_API,
                           ok=False, error="robots.txt disallows fetch", manual_only=True)

    ms = company_cfg.get("microsoft", {})
    queries = ms.get("queries") or DEFAULT_QUERIES

    jobs, seen = [], set()
    try:
        for query in queries:
            start = 0
            for _ in range(MAX_PAGES):
                resp = http_get(SEARCH_API, params={
                    "domain": DOMAIN, "query": query,
                    "start": start, "num": PAGE_SIZE,
                }, headers={"Accept": "application/json",
                            "Referer": CAREERS_PAGE})
                if resp.status_code != 200:
                    if start == 0 and not jobs:
                        return FetchResult(company=company_cfg["name"], tier_used=3,
                                           source=SEARCH_API, ok=False,
                                           error=f"HTTP {resp.status_code}")
                    break
                payload = resp.json().get("data", {}) or {}
                positions = payload.get("positions", []) or []
                if not positions:
                    break
                for raw in positions:
                    ext_id = str(raw.get("displayJobId") or raw.get("id") or "")
                    if not ext_id or ext_id in seen:
                        continue        # same req appears across multiple queries
                    seen.add(ext_id)
                    locations = raw.get("locations") or []
                    url_path = raw.get("positionUrl") or ""
                    # No JD in the list payload — give the scorer what signal exists.
                    context = " ".join(filter(None, [
                        raw.get("name", ""),
                        raw.get("department", ""),
                        raw.get("workLocationOption", ""),
                    ]))
                    jobs.append({
                        "company": company_cfg["display_name"],
                        "title": raw.get("name", ""),
                        "location": "; ".join(locations),
                        "description": context,
                        "apply_link": (f"https://apply.careers.microsoft.com{url_path}"
                                       if url_path.startswith("/") else url_path),
                        "posted_date": _iso(raw.get("postedTs") or raw.get("creationTs")),
                        "external_id": ext_id,
                        "source_tier": 3,
                    })
                start += PAGE_SIZE
                if start >= int(payload.get("count", 0)):
                    break
    except Exception as e:
        return FetchResult(company=company_cfg["name"], tier_used=3, source=SEARCH_API,
                           ok=False, error=str(e))

    return FetchResult(company=company_cfg["name"], tier_used=3, source=SEARCH_API,
                       ok=True, jobs=jobs)
