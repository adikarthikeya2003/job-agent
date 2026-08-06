"""Tier 3 — generic HTML scraping fallback for companies with no JSON API.

Only used when a company config sets adapter: "html" with a `careers_url` and CSS
selectors. Always checks robots.txt first and refuses to scrape if disallowed —
returns manual_only=True instead. This is the most fragile fetch method that still
runs in the automated daily cycle (breaks on any DOM/markup change), so use Tier 1/2
whenever a company offers them.
"""
from bs4 import BeautifulSoup

from .base import FetchResult, http_get, robots_allows


def fetch(company_cfg: dict) -> FetchResult:
    scrape_cfg = company_cfg.get("html", {})
    url = scrape_cfg.get("careers_url")
    if not url:
        return FetchResult(company=company_cfg["name"], tier_used=3, source="",
                            ok=False, error="html.careers_url not configured")

    if not robots_allows(url):
        return FetchResult(company=company_cfg["name"], tier_used=3, source=url,
                            ok=False, error="robots.txt disallows fetch", manual_only=True)

    try:
        resp = http_get(url)
    except Exception as e:
        return FetchResult(company=company_cfg["name"], tier_used=3, source=url,
                            ok=False, error=str(e))
    if resp.status_code != 200:
        return FetchResult(company=company_cfg["name"], tier_used=3, source=url,
                            ok=False, error=f"HTTP {resp.status_code}")

    soup = BeautifulSoup(resp.text, "lxml")
    row_selector = scrape_cfg.get("row_selector")
    title_selector = scrape_cfg.get("title_selector", "")
    link_selector = scrape_cfg.get("link_selector", "")
    location_selector = scrape_cfg.get("location_selector", "")

    if not row_selector:
        return FetchResult(company=company_cfg["name"], tier_used=3, source=url, ok=False,
                            error="html.row_selector not configured — this adapter needs "
                                  "per-company CSS selectors, it cannot guess page structure")

    jobs = []
    for row in soup.select(row_selector):
        title_el = row.select_one(title_selector) if title_selector else row
        link_el = row.select_one(link_selector) if link_selector else row.find("a")
        loc_el = row.select_one(location_selector) if location_selector else None

        title = title_el.get_text(strip=True) if title_el else ""
        href = link_el.get("href", "") if link_el else ""
        if href and href.startswith("/"):
            from urllib.parse import urljoin
            href = urljoin(url, href)

        if not title:
            continue
        jobs.append({
            "company": company_cfg["display_name"],
            "title": title,
            "location": loc_el.get_text(strip=True) if loc_el else "",
            "description": "",
            "apply_link": href,
            "posted_date": "",
            "external_id": href or title,
            "source_tier": 3,
        })
    return FetchResult(company=company_cfg["name"], tier_used=3, source=url, ok=True, jobs=jobs)
