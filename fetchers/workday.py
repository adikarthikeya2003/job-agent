"""Tier 3 — generic Workday CXS JSON adapter.

Many large enterprises run their career site on Workday, which exposes an internal
JSON endpoint used by the site's own React frontend:

  POST https://{tenant}.{wd_host}/wday/cxs/{tenant}/{site}/jobs
  body: {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}

This is NOT an officially published/supported API — it's the same call the career
site's browser makes, discoverable via devtools network tab, and it is what most
third-party job aggregators use for Workday-hosted employers. It can change without
notice and tenant/site/host values are per-company (set in config.yaml). Gated
behind a live robots.txt check before every call, per the daily-run Tier 1-3 policy.

Dell Technologies is configured here as a best-effort target (tenant "dell", host
"dell.wd1.myworkdayjobs.com") — this configuration is UNVERIFIED. Confirm the real
tenant/site by visiting Dell's career site, opening browser devtools -> Network,
and finding the actual `/wday/cxs/.../jobs` request before relying on this in the
daily run. If it 404s or times out, the pipeline logs a failure for "dell" and moves
on rather than guessing.
"""
import re
from datetime import datetime, timedelta

from .base import FetchResult, http_post, robots_allows

JOBS_PER_PAGE = 20

_REL_DAYS_RE = re.compile(r"posted\s+(\d+)\+?\s+days?\s+ago", re.I)


def iso_posted_date(raw: str) -> str:
    """Workday returns relative strings ('Posted Yesterday', 'Posted 30+ Days Ago')
    rather than a date. Convert to ISO so posted_date is comparable and displayable
    alongside the other adapters, which all emit real dates.

    '30+ Days Ago' is a floor, not an exact age — Workday caps the counter there — so
    the resulting date is the *newest* it could be. Unparseable input is passed through
    unchanged rather than guessed at."""
    if not raw:
        return ""
    text = raw.strip()
    low = text.lower()
    today = datetime.now()
    if "today" in low or "just posted" in low:
        return today.strftime("%Y-%m-%d")
    if "yesterday" in low:
        return (today - timedelta(days=1)).strftime("%Y-%m-%d")
    m = _REL_DAYS_RE.search(low)
    if m:
        return (today - timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
    return text


def fetch(company_cfg: dict) -> FetchResult:
    wd = company_cfg.get("workday", {})
    tenant = wd.get("tenant")
    wd_host = wd.get("wd_host")
    site = wd.get("site", "External")
    if not (tenant and wd_host):
        return FetchResult(company=company_cfg["name"], tier_used=3, source="",
                            ok=False, error="workday.tenant/wd_host not configured")

    url = f"https://{wd_host}/wday/cxs/{tenant}/{site}/jobs"
    page_url = f"https://{wd_host}/{site}"
    if not robots_allows(page_url):
        return FetchResult(company=company_cfg["name"], tier_used=3, source=url,
                            ok=False, error="robots.txt disallows fetch", manual_only=True)

    jobs = []
    offset = 0
    total = None      # Workday reports `total` on the FIRST page only; later pages send 0.
    max_offset = wd.get("max_offset", 2000)   # safety stop if a tenant never returns empty
    try:
        while True:
            # Longer timeout than the 20s default: a full board is ~30 sequential calls
            # and Workday tenants are slow enough that 20s intermittently trips.
            resp = http_post(url, json={
                "appliedFacets": {}, "limit": JOBS_PER_PAGE, "offset": offset, "searchText": "",
            }, timeout=45)
            if resp.status_code != 200:
                if offset == 0:
                    return FetchResult(company=company_cfg["name"], tier_used=3, source=url,
                                        ok=False, error=f"HTTP {resp.status_code}")
                break
            data = resp.json()
            if total is None:
                total = data.get("total", 0) or 0
            postings = data.get("jobPostings", [])
            if not postings:
                break
            for raw in postings:
                path = raw.get("externalPath", "")
                jobs.append({
                    "company": company_cfg["display_name"],
                    "title": raw.get("title", ""),
                    "location": raw.get("locationsText", ""),
                    "description": "",  # requires a per-job detail fetch; left blank at list stage
                    "apply_link": f"https://{wd_host}/{site}{path}" if path else "",
                    "posted_date": iso_posted_date(raw.get("postedOn", "")),
                    "external_id": raw.get("bulletFields", [""])[0] if raw.get("bulletFields") else path,
                    "source_tier": 3,
                })
            offset += JOBS_PER_PAGE
            if offset >= total or offset >= max_offset:
                break
    except Exception as e:
        return FetchResult(company=company_cfg["name"], tier_used=3, source=url,
                            ok=False, error=str(e))

    return FetchResult(company=company_cfg["name"], tier_used=3, source=url, ok=True, jobs=jobs)
