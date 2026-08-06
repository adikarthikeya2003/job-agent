"""Tier 2 — company-published RSS/Atom job feeds.

Uses stdlib xml.etree instead of adding a feedparser dependency, since project deps
are already covered by requests/bs4/lxml/sklearn. Company feed URLs live in
config.yaml under companies[].rss_url. Most large tech employers (Dell/Meta/Google
included) do not publish a job RSS feed, so this tier is mainly useful for smaller
companies or third-party-published feeds you've explicitly configured.
"""
import xml.etree.ElementTree as ET

from .base import FetchResult, http_get


def fetch(company_cfg: dict) -> FetchResult:
    url = company_cfg.get("rss_url")
    if not url:
        return FetchResult(company=company_cfg["name"], tier_used=2, source="",
                            ok=False, error="no rss_url configured")
    try:
        resp = http_get(url)
    except Exception as e:
        return FetchResult(company=company_cfg["name"], tier_used=2, source=url,
                            ok=False, error=str(e))
    if resp.status_code != 200:
        return FetchResult(company=company_cfg["name"], tier_used=2, source=url,
                            ok=False, error=f"HTTP {resp.status_code}")
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        return FetchResult(company=company_cfg["name"], tier_used=2, source=url,
                            ok=False, error=f"XML parse error: {e}")

    jobs = []
    items = root.findall(".//item") or root.findall(
        ".//{http://www.w3.org/2005/Atom}entry")
    for item in items:
        title_el = item.find("title") or item.find("{http://www.w3.org/2005/Atom}title")
        link_el = item.find("link") or item.find("{http://www.w3.org/2005/Atom}link")
        desc_el = item.find("description") or item.find(
            "{http://www.w3.org/2005/Atom}summary")
        pub_el = item.find("pubDate") or item.find("{http://www.w3.org/2005/Atom}updated")

        link = link_el.text if link_el is not None and link_el.text else (
            link_el.get("href") if link_el is not None else "")
        jobs.append({
            "company": company_cfg["display_name"],
            "title": title_el.text if title_el is not None else "",
            "location": "",
            "description": desc_el.text if desc_el is not None and desc_el.text else "",
            "apply_link": link or "",
            "posted_date": pub_el.text if pub_el is not None else "",
            "external_id": link or "",
            "source_tier": 2,
        })
    return FetchResult(company=company_cfg["name"], tier_used=2, source=url, ok=True, jobs=jobs)
