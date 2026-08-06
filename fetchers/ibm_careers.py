"""Tier 3 — IBM careers search adapter.

careers.ibm.com is Avature-hosted and answers direct requests with an HTTP 202
bot-challenge, so it cannot be scraped. The ibm.com/careers/search page instead posts
to IBM's own Elasticsearch-backed search service:

  POST https://www-api.ibm.com/search/api/v2

HOST MATTERS: the browser's request goes to `www-api.ibm.com`, not `www.ibm.com`.
Posting to www.ibm.com returns a 404 edge-rewrite artifact ({"matched_rule": ...}).

The query DSL is restricted — `query_string` is rejected with HTTP 400 ("Invalid
value"). `match` and an empty `bool.must` both work. Results are returned as bare
`_id`s unless an explicit `_source` field list is supplied, which is why FIELDS below
is sent on every call.

NO POSTED DATE: IBM's search index exposes no date/timestamp field on job documents —
requesting every plausible date key returns nothing. `posted_date` is therefore left
empty for IBM rather than fabricated, and renders as "unknown" downstream. This is a
real limitation of the source, not a bug in this adapter.
"""
from .base import FetchResult, http_post, robots_allows

SEARCH_API = "https://www-api.ibm.com/search/api/v2"
CAREERS_PAGE = "https://www.ibm.com/careers/search"
PAGE_SIZE = 30
MAX_PAGES = 40

FIELDS = ["_id", "title", "url", "description",
          "body",               # FULL JD text; `description` is only a ~250-char blurb
                                # with no qualifications, so it never states a YoE bar.
          "field_keyword_05",   # country, e.g. "United States"
          "field_keyword_17",   # work mode, e.g. "Hybrid"
          "field_keyword_18",   # experience level, e.g. "Entry Level"
          "field_keyword_19"]   # city, e.g. "Austin, TX"


def fetch(company_cfg: dict) -> FetchResult:
    if not robots_allows(CAREERS_PAGE):
        return FetchResult(company=company_cfg["name"], tier_used=3, source=SEARCH_API,
                           ok=False, error="robots.txt disallows fetch", manual_only=True)

    ib = company_cfg.get("ibm", {})
    # Empty bool.must pulls the whole public careers index (a few hundred postings) and
    # lets our own scorer rank them — consistent with how the Greenhouse/Lever adapters
    # work, rather than pre-filtering by title at the source.
    must = []
    if ib.get("title_query"):
        must.append({"match": {"title": ib["title_query"]}})

    headers = {"Accept": "application/json",
               "Referer": "https://www.ibm.com/",
               "Origin": "https://www.ibm.com"}

    jobs, seen = [], set()
    offset = 0
    try:
        for _ in range(MAX_PAGES):
            body = {
                "appId": "careers",
                "scopes": ["careers2"],
                "query": {"bool": {"must": must}},
                "size": PAGE_SIZE,
                "from": offset,
                "lang": "en",
                "_source": FIELDS,
                "sort": [{"_score": "desc"}],
            }
            resp = http_post(SEARCH_API, json=body, headers=headers)
            if resp.status_code != 200:
                if offset == 0 and not jobs:
                    return FetchResult(company=company_cfg["name"], tier_used=3,
                                       source=SEARCH_API, ok=False,
                                       error=f"HTTP {resp.status_code}")
                break
            hits_obj = resp.json().get("hits", {}) or {}
            hits = hits_obj.get("hits", []) or []
            if not hits:
                break
            for hit in hits:
                src = hit.get("_source", {}) or {}
                ext_id = hit.get("_id", "")
                if not ext_id or ext_id in seen:
                    continue
                seen.add(ext_id)
                city = src.get("field_keyword_19", "") or ""
                country = src.get("field_keyword_05", "") or ""
                # Join city + country so the US filter sees an explicit country name
                # ("Bangalore, IN" alone is ambiguous; "Bangalore, IN, India" is not).
                location = ", ".join(p for p in (city, country) if p)
                jobs.append({
                    "company": company_cfg["display_name"],
                    "title": src.get("title", ""),
                    "location": location,
                    "description": (src.get("body", "") or src.get("description", "") or ""),
                    "apply_link": src.get("url", ""),
                    "posted_date": "",     # not published by the source — see module docstring
                    "external_id": ext_id,
                    "source_tier": 3,
                })
            total = (hits_obj.get("total") or {}).get("value", 0)
            offset += PAGE_SIZE
            if offset >= total:
                break
    except Exception as e:
        return FetchResult(company=company_cfg["name"], tier_used=3, source=SEARCH_API,
                           ok=False, error=str(e))

    return FetchResult(company=company_cfg["name"], tier_used=3, source=SEARCH_API,
                       ok=True, jobs=jobs)
