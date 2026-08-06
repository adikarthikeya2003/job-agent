"""Tier 3 — amazon.jobs public search JSON adapter.

amazon.jobs' own frontend calls a public, unauthenticated JSON endpoint:

  GET https://www.amazon.jobs/search.json
      ?base_query={q}&loc_query={location}&result_limit={n}&offset={n}&sort=recent

Unofficial and undocumented (same fragility class as the Workday/Google adapters),
but it is stable, returns full JD text inline (no per-job detail fetch needed), and
covers Amazon + AWS + subsidiaries.

FRESHNESS: `sort=recent` is always sent so pagination walks newest-first, and every
run re-queries live — nothing is served from cache. `posted_date` arrives as a
human string ("July 31, 2026") and is normalized to ISO here so downstream date
filtering and display are consistent with the other adapters.
"""
import re
from datetime import datetime

from .base import FetchResult, http_get, robots_allows

SEARCH_API = "https://www.amazon.jobs/search.json"
CAREERS_PAGE = "https://www.amazon.jobs/en/search"
PAGE_SIZE = 100          # endpoint's practical max per call
MAX_PAGES = 10           # hard cap so one company can't dominate a run

# Queries are role-targeted rather than a blind full-board pull: Amazon has tens of
# thousands of open reqs (mostly warehouse/ops) and fetching all of them would swamp
# the scorer with noise. These map to the role families in config.yaml.
DEFAULT_QUERIES = [
    "data engineer",
    "data scientist",
    "data analyst",
    "machine learning engineer",
    "business intelligence engineer",
]


def _iso_date(raw: str) -> str:
    """'July 31, 2026' -> '2026-07-31'. Returns the input unchanged if unparseable."""
    if not raw:
        return ""
    raw = raw.strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


_TAG_RE = re.compile(r"<[^>]+>")


def _plain(html_text: str) -> str:
    return _TAG_RE.sub(" ", html_text or "").replace("&nbsp;", " ")


def fetch(company_cfg: dict) -> FetchResult:
    if not robots_allows(CAREERS_PAGE):
        return FetchResult(company=company_cfg["name"], tier_used=3, source=SEARCH_API,
                           ok=False, error="robots.txt disallows fetch", manual_only=True)

    az = company_cfg.get("amazon", {})
    queries = az.get("queries") or DEFAULT_QUERIES
    loc_query = az.get("loc_query", "United States")

    jobs, seen_ids = [], set()
    try:
        for query in queries:
            offset = 0
            for _ in range(MAX_PAGES):
                resp = http_get(SEARCH_API, params={
                    "base_query": query,
                    "loc_query": loc_query,
                    "country": "USA",
                    "result_limit": PAGE_SIZE,
                    "offset": offset,
                    "sort": "recent",
                })
                if resp.status_code != 200:
                    if offset == 0 and not jobs:
                        return FetchResult(company=company_cfg["name"], tier_used=3,
                                           source=SEARCH_API, ok=False,
                                           error=f"HTTP {resp.status_code}")
                    break
                data = resp.json()
                batch = data.get("jobs", []) or []
                if not batch:
                    break
                for raw in batch:
                    ext_id = str(raw.get("id_icims") or raw.get("id") or "")
                    if not ext_id or ext_id in seen_ids:
                        continue          # same req surfaces across multiple queries
                    seen_ids.add(ext_id)
                    path = raw.get("job_path", "")
                    description = " ".join(filter(None, [
                        _plain(raw.get("description", "")),
                        _plain(raw.get("basic_qualifications", "")),
                        _plain(raw.get("preferred_qualifications", "")),
                    ]))
                    jobs.append({
                        "company": company_cfg["display_name"],
                        "title": raw.get("title", ""),
                        "location": raw.get("normalized_location") or raw.get("location", ""),
                        "description": description,
                        "apply_link": f"https://www.amazon.jobs{path}" if path else
                                      raw.get("url_next_step", ""),
                        "posted_date": _iso_date(raw.get("posted_date", "")),
                        "external_id": ext_id,
                        "source_tier": 3,
                    })
                offset += PAGE_SIZE
                if offset >= int(data.get("hits", 0)):
                    break
    except Exception as e:
        return FetchResult(company=company_cfg["name"], tier_used=3, source=SEARCH_API,
                           ok=False, error=str(e))

    return FetchResult(company=company_cfg["name"], tier_used=3, source=SEARCH_API,
                       ok=True, jobs=jobs)
