"""Tier 3 — Oracle Fusion Cloud Recruiting (Candidate Experience) adapter.

Dell Technologies' actual career site (jobs.dell.com) redirects to Oracle Fusion Cloud
HCM Candidate Experience, NOT Workday — confirmed live by following the redirect and
inspecting the rendered page's asset URLs for a `siteNumber` query param. This replaced
an earlier, incorrect assumption that Dell ran on Workday (which returned genuine but
empty results — a real tenant/site that just isn't the one Dell uses).

Endpoint (unauthenticated, JSON, used by the site's own frontend — unofficial/undocumented
but a well-known pattern across Oracle Fusion Recruiting Cloud career sites generally):

  GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions
      ?onlyData=true
      &expand=requisitionList.secondaryLocations,flexFieldsFacet.values
      &finder=findReqs;siteNumber={site_number},limit={n},offset={o},sortBy=POSTING_DATES_DESC

Confirmed live for Dell: host=iawmqy.fa.ocs.oraclecloud.com, siteNumber=CX_1001,
TotalJobsCount=348 at time of verification. This host publishes no robots.txt (404),
which is treated as permissive by convention (same rule as every other Tier 3 adapter).
"""
from .base import FetchResult, http_get, robots_allows

PAGE_LIMIT = 50


def fetch(company_cfg: dict) -> FetchResult:
    oracle = company_cfg.get("oracle_hcm", {})
    host = oracle.get("host")
    site_number = oracle.get("site_number")
    if not (host and site_number):
        return FetchResult(company=company_cfg["name"], tier_used=3, source="",
                            ok=False, error="oracle_hcm.host/site_number not configured")

    base_url = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    careers_page = f"https://{host}/hcmUI/CandidateExperience/en/sites/careers/jobs"
    if not robots_allows(careers_page):
        return FetchResult(company=company_cfg["name"], tier_used=3, source=base_url,
                            ok=False, error="robots.txt disallows fetch", manual_only=True)

    jobs = []
    offset = 0
    try:
        while True:
            finder = (f"findReqs;siteNumber={site_number},limit={PAGE_LIMIT},"
                      f"offset={offset},sortBy=POSTING_DATES_DESC")
            resp = http_get(base_url, params={
                "onlyData": "true",
                "expand": "requisitionList.secondaryLocations,flexFieldsFacet.values",
                "finder": finder,
            })
            if resp.status_code != 200:
                if offset == 0:
                    return FetchResult(company=company_cfg["name"], tier_used=3, source=base_url,
                                        ok=False, error=f"HTTP {resp.status_code}")
                break
            data = resp.json()
            items = data.get("items", [])
            if not items:
                break
            item = items[0]
            reqs = item.get("requisitionList", [])
            total = item.get("TotalJobsCount", 0)
            if not reqs:
                break
            for r in reqs:
                rid = r.get("Id", "")
                desc = " ".join(filter(None, [
                    r.get("ShortDescriptionStr", ""),
                    r.get("ExternalResponsibilitiesStr", ""),
                    r.get("ExternalQualificationsStr", ""),
                ]))
                jobs.append({
                    "company": company_cfg["display_name"],
                    "title": r.get("Title", ""),
                    "location": r.get("PrimaryLocation", ""),
                    "description": desc,
                    "apply_link": f"https://{host}/hcmUI/CandidateExperience/en/sites/careers/job/{rid}",
                    "posted_date": r.get("PostedDate", ""),
                    "external_id": str(rid),
                    "source_tier": 3,
                })
            offset += PAGE_LIMIT
            if offset >= total:
                break
    except Exception as e:
        return FetchResult(company=company_cfg["name"], tier_used=3, source=base_url,
                            ok=False, error=str(e))

    return FetchResult(company=company_cfg["name"], tier_used=3, source=base_url,
                        ok=True, jobs=jobs)
