"""Tier 2 — GitHub-hosted job-listing markdown (e.g. speedyapply/2027-AI-College-Jobs).

These community repos publish a curated, frequently-updated table of new-grad/intern
postings as a markdown file. We fetch the *raw* file fresh on every run (no caching, so
each fetch reflects the repo's current state) and parse its HTML-in-markdown table rows.

Each data row looks like:
  | <a href="COMPANY_SITE"><strong>Company</strong></a> | Position | Location | Salary |
    <a href="APPLY_URL"><img .../></a> | Age |

The "Age" column is a relative posting age like "9d" / "3mo" / "1d"; we convert it to an
absolute posted_date so downstream output/dedup behave like every other tier. There is no
per-posting job description in these tables, so match scoring runs on the title (enriched
with location + salary) — expect scores on a slightly different scale than full-JD sources.
"""
import re
from datetime import datetime, timedelta

from .base import FetchResult, http_get

# raw.githubusercontent.com URL is built from the repo's blob/HTML url in config.
RAW_HOST = "https://raw.githubusercontent.com"

_ANCHOR_HREF_RE = re.compile(r'href="([^"]+)"', re.I)
_STRONG_TEXT_RE = re.compile(r"<strong>(.*?)</strong>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_AGE_RE = re.compile(r"(\d+)\s*(d|w|mo|m|y|h)", re.I)


def _strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text).strip()


def _company_name(cell: str) -> str:
    m = _STRONG_TEXT_RE.search(cell)
    if m:
        return _strip_tags(m.group(1)).strip()
    return _strip_tags(cell).strip()


def _apply_link(cell: str) -> str:
    m = _ANCHOR_HREF_RE.search(cell)
    return m.group(1).strip() if m else ""


def _parse_row(line: str) -> dict | None:
    """Parse one markdown table data row into a job dict, tolerating both the 6-column
    layout (Company | Position | Location | Salary | Apply | Age) and the 5-column layout
    (Company | Position | Location | Apply | Age) used by tables without a salary column.
    Returns None for header/separator/malformed rows."""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if len(cells) < 5:
        return None
    company = _company_name(cells[0])
    position = _strip_tags(cells[1])
    location = _strip_tags(cells[2])
    if not company or not position:
        return None
    age = cells[-1]
    # The apply cell is the one wrapping the "Apply" image; company cell also has an <a>,
    # so key off the <img> that only the apply button carries.
    apply_idx = next((i for i, c in enumerate(cells) if "<img" in c.lower()), None)
    if apply_idx is None:
        # Fallback: last anchor-bearing cell before the age column.
        apply_idx = next((i for i in range(len(cells) - 2, 2, -1)
                          if _ANCHOR_HREF_RE.search(cells[i])), None)
    apply_link = _apply_link(cells[apply_idx]) if apply_idx is not None else ""
    # A salary cell sits between location (index 2) and the apply cell only in the 6-col form.
    salary = cells[3] if (apply_idx is not None and apply_idx > 3) else ""
    salary = _strip_tags(salary)
    return {"company": company, "position": position, "location": location,
            "salary": salary, "apply_link": apply_link, "age": age}


def _age_to_posted_date(age: str, now: datetime) -> str:
    """Convert a relative age token ('9d', '3mo', '1w', '2h', '1y') to an ISO date.
    Unknown/blank -> "" (treated as unknown posted_date downstream)."""
    age = _strip_tags(age)
    m = _AGE_RE.search(age)
    if not m:
        return ""
    n = int(m.group(1))
    unit = m.group(2).lower()
    days = {
        "h": n / 24.0,
        "d": n,
        "w": n * 7,
        "mo": n * 30,
        "m": n * 30,   # some repos write months as "m"
        "y": n * 365,
    }.get(unit, 0)
    return (now - timedelta(days=days)).date().isoformat()


def _to_raw_url(url: str, branch: str) -> str:
    """Accepts either a github.com blob URL or an already-raw URL and returns a
    raw.githubusercontent.com URL that serves the plain markdown."""
    if url.startswith(RAW_HOST):
        return url
    # https://github.com/{owner}/{repo}/blob/{branch}/{path}  ->  raw host
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)", url)
    if m:
        owner, repo, ref, path = m.groups()
        return f"{RAW_HOST}/{owner}/{repo}/{ref}/{path}"
    # https://github.com/{owner}/{repo}/.../{path} without /blob/ — best effort
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/(.+)", url)
    if m:
        owner, repo, path = m.groups()
        path = re.sub(r"^(blob|tree|raw)/[^/]+/", "", path)
        return f"{RAW_HOST}/{owner}/{repo}/{branch}/{path}"
    return url


def fetch(company_cfg: dict) -> FetchResult:
    gh = company_cfg.get("github_md", {})
    src_url = gh.get("url", "")
    branch = gh.get("branch", "main")
    if not src_url:
        return FetchResult(company=company_cfg["name"], tier_used=2, source="",
                           ok=False, error="github_md.url missing in config")
    raw_url = _to_raw_url(src_url, branch)
    try:
        resp = http_get(raw_url, timeout=25)
    except Exception as e:
        return FetchResult(company=company_cfg["name"], tier_used=2, source=raw_url,
                           ok=False, error=str(e))
    if resp.status_code != 200:
        return FetchResult(company=company_cfg["name"], tier_used=2, source=raw_url,
                           ok=False, error=f"HTTP {resp.status_code}")

    now = datetime.now()
    jobs = []
    for line in resp.text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        # Skip header + separator rows.
        low = line.lower()
        if low.startswith("| company") or set(low) <= set("|-: "):
            continue
        row = _parse_row(line)
        if row is None:
            continue
        posted_date = _age_to_posted_date(row["age"], now)
        # No JD text in these tables — enrich the scored text with location + salary so the
        # matcher has a little more signal than the bare title.
        description = f"{row['position']}. Location: {row['location']}. Salary: {row['salary']}."
        external_id = row["apply_link"] or f"{row['company']}:{row['position']}:{row['location']}"
        jobs.append({
            "company": f"{row['company']} (via {company_cfg['display_name']})",
            "title": row["position"],
            "location": row["location"],
            "description": description,
            "apply_link": row["apply_link"],
            "posted_date": posted_date,
            "salary": row["salary"],
            "external_id": external_id,
            "source_tier": 2,
        })
    return FetchResult(company=company_cfg["name"], tier_used=2, source=raw_url,
                       ok=True, jobs=jobs)
