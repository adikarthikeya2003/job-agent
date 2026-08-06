"""Tier 3 — Google Careers search adapter.

careers.google.com is a JS-rendered SPA with no officially published third-party API.
Its own frontend calls a public search endpoint:

  GET https://careers.google.com/api/v3/search/?q={query}&page={n}

This endpoint is unauthenticated and returns JSON, but it is UNDOCUMENTED and
UNOFFICIAL — Google can change or remove it without notice, and relying on it
carries real fragility risk (this is the same risk class as the Workday adapter,
just for a bespoke platform instead of a common ATS). Gated behind a live
robots.txt check before every call; if disallowed, the pipeline marks Google as
manual-apply-only for that run instead of forcing the request.
"""
from .base import FetchResult, http_get, robots_allows

SEARCH_API = "https://careers.google.com/api/v3/search/"
CAREERS_PAGE = "https://careers.google.com/jobs/results/"


def fetch(company_cfg: dict, query: str = "", page: int = 1) -> FetchResult:
    if not robots_allows(CAREERS_PAGE):
        return FetchResult(company=company_cfg["name"], tier_used=3, source=SEARCH_API,
                            ok=False, error="robots.txt disallows fetch", manual_only=True)
    try:
        resp = http_get(SEARCH_API, params={"q": query, "page": page})
    except Exception as e:
        return FetchResult(company=company_cfg["name"], tier_used=3, source=SEARCH_API,
                            ok=False, error=str(e))
    if resp.status_code != 200:
        return FetchResult(company=company_cfg["name"], tier_used=3, source=SEARCH_API,
                            ok=False, error=f"HTTP {resp.status_code} "
                                             f"(endpoint likely changed/blocked — treat as fragile)")

    try:
        data = resp.json()
    except ValueError:
        return FetchResult(company=company_cfg["name"], tier_used=3, source=SEARCH_API,
                            ok=False, error="non-JSON response — endpoint shape changed")

    jobs = []
    for raw in data.get("jobs", []):
        locations = raw.get("locations", []) or []
        loc_str = "; ".join(
            f"{loc.get('city', '')}, {loc.get('state', '')}".strip(", ")
            for loc in locations
        )
        jobs.append({
            "company": company_cfg["display_name"],
            "title": raw.get("title", ""),
            "location": loc_str,
            "description": raw.get("summary", "") or "",
            "apply_link": f"https://careers.google.com/jobs/results/{raw.get('id', '')}/"
                          if raw.get("id") else "",
            "posted_date": raw.get("publish_date", ""),
            "external_id": str(raw.get("id", "")),
            "source_tier": 3,
        })
    return FetchResult(company=company_cfg["name"], tier_used=3, source=SEARCH_API,
                        ok=True, jobs=jobs)
