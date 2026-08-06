"""Per-job detail fetch, used only to recover the stated experience requirement.

Several sources publish no job-description body in their LIST payload (Microsoft's
Eightfold search, Workday CXS, Oracle HCM), so `tag_experience_required` has nothing
to read and honestly reports "not stated". That is useless to a job seeker deciding
whether a posting is even open to them, so after scoring we re-fetch the JD for the
handful of postings that actually get surfaced and extract the requirement from the
real text.

Scope is deliberately narrow:
  - Only surfaced jobs (score >= threshold) missing an experience value are fetched,
    which is tens of requests per run, not thousands. A full-board detail crawl would
    be ~2,500 extra requests against employer sites for no added ranking value.
  - Failures are swallowed per job and leave "not stated" in place. A detail endpoint
    breaking must never take down a run.
  - Match scores are NOT recomputed from the enriched text: scoring stays list-based
    and comparable across sources within a run.
"""
import logging
import re

from .base import http_get, http_post

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'"))
    return _WS_RE.sub(" ", text).strip()


def _microsoft(job: dict, company_cfg: dict) -> str:
    """Eightfold publishes the full JD at /api/apply/v2/jobs/<pid>, keyed by the numeric
    position id that ends the apply_link (the list payload's displayJobId will NOT work)."""
    link = job.get("apply_link", "")
    m = re.search(r"/job/(\d+)", link)
    if not m:
        return ""
    pid = m.group(1)
    resp = http_get(f"https://apply.careers.microsoft.com/api/apply/v2/jobs/{pid}",
                    params={"domain": "microsoft.com"},
                    headers={"Accept": "application/json", "Referer": link})
    if resp.status_code != 200:
        return ""
    data = resp.json()
    return _strip_html(" ".join(filter(None, [
        data.get("job_description", ""),
        data.get("qualifications", ""),
    ])))


def _workday(job: dict, company_cfg: dict) -> str:
    """Workday CXS exposes the JD at the same /wday/cxs/<tenant>/<site> prefix as the
    list call, with the posting's externalPath appended."""
    wd = company_cfg.get("workday", {})
    tenant, wd_host = wd.get("tenant"), wd.get("wd_host")
    site = wd.get("site", "External")
    link = job.get("apply_link", "")
    if not (tenant and wd_host and link):
        return ""
    path = link.split(f"/{site}", 1)[-1]
    if not path.startswith("/"):
        return ""
    resp = http_get(f"https://{wd_host}/wday/cxs/{tenant}/{site}{path}",
                    headers={"Accept": "application/json"}, timeout=45)
    if resp.status_code != 200:
        return ""
    info = resp.json().get("jobPostingInfo", {}) or {}
    return _strip_html(info.get("jobDescription", ""))


def _oracle_hcm(job: dict, company_cfg: dict) -> str:
    """Oracle Candidate Experience detail. Note the resource is the PLURAL
    ...RequisitionDetails and the finder Id must be quoted — the singular form and an
    unquoted Id both 404."""
    oc = company_cfg.get("oracle_hcm", {})
    host, site_number = oc.get("host"), oc.get("site_number")
    rid = job.get("external_id", "")
    if not (host and site_number and rid):
        return ""
    url = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
    resp = http_get(url, params={
        "onlyData": "true", "expand": "all",
        "finder": f'ById;Id="{rid}",siteNumber={site_number}',
    }, headers={"Accept": "application/json"})
    if resp.status_code != 200:
        return ""
    items = resp.json().get("items", []) or []
    if not items:
        return ""
    item = items[0]
    return _strip_html(" ".join(filter(None, [
        item.get("ExternalResponsibilitiesStr", ""),
        item.get("ExternalQualificationsStr", ""),
        item.get("ShortDescriptionStr", ""),
    ])))


# Keyed by the adapter name in config.yaml. A source absent here simply never gets a
# detail pass — its list payload is either already complete or has no detail endpoint.
DETAIL_FETCHERS = {
    "microsoft_careers": _microsoft,
    "workday": _workday,
    "oracle_hcm": _oracle_hcm,
}


def supports(adapter_name: str) -> bool:
    return adapter_name in DETAIL_FETCHERS


def fetch_description(job: dict, company_cfg: dict) -> str:
    """Return the full JD text for one posting, or "" if unavailable.

    Never raises: a broken or rate-limited detail endpoint degrades to "not stated"
    rather than failing the run.
    """
    fn = DETAIL_FETCHERS.get(company_cfg.get("adapter", ""))
    if fn is None:
        return ""
    try:
        return fn(job, company_cfg)
    except Exception as e:                      # noqa: BLE001 - deliberate catch-all
        logger.debug(f"detail fetch failed for {job.get('title', '?')}: {e}")
        return ""
