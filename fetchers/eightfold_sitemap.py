"""Tier 3 — Eightfold-hosted career sites via public sitemap + per-job JSON-LD.

Eightfold (careers.<company>.com, e.g. Qualcomm) exposes no usable public search API —
/api/apply/v2/jobs returns "Not authorized" without a session. But the site publishes
a full sitemap.xml (robots.txt-allowed) listing every open posting as a URL slug with
a lastmod date, and each job page embeds a clean schema.org JobPosting JSON-LD block
(title, jobLocation, description, datePosted) that needs no auth.

Crawling all ~700+ US slugs' JSON-LD per run would be hundreds of requests for a single
company, dwarfing every other adapter. So this pre-filters candidates from the sitemap
slug text alone (title keywords configured per-company in config.yaml) before ever
fetching a job page — the real relevance scoring still happens downstream in
match_scorer.py against the resume; this filter only keeps the per-job fetch count in
the tens, not the hundreds.
"""
import re
import xml.etree.ElementTree as ET

from .base import FetchResult, http_get, robots_allows

_JOB_ID_RE = re.compile(r"/job/(\d+)-")
_LD_JSON_RE = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>', re.S)


def _matching_slugs(sitemap_xml: str, keywords: list) -> list:
    root = ET.fromstring(sitemap_xml)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = []
    for url_el in root.findall("sm:url", ns) or root.findall("url"):
        loc_el = url_el.find("sm:loc", ns) if url_el.find("sm:loc", ns) is not None else url_el.find("loc")
        if loc_el is None or not loc_el.text:
            continue
        loc = loc_el.text
        if "/job/" not in loc or "united-states-of-america" not in loc.lower():
            continue
        low = loc.lower()
        if keywords and not any(kw.lower() in low for kw in keywords):
            continue
        urls.append(loc)
    return urls


def fetch(company_cfg: dict) -> FetchResult:
    import json

    cfg = company_cfg.get("eightfold_sitemap", {})
    sitemap_url = cfg.get("sitemap_url")
    keywords = cfg.get("keywords", [])
    if not sitemap_url:
        return FetchResult(company=company_cfg["name"], tier_used=3, source="",
                            ok=False, error="eightfold_sitemap.sitemap_url not configured")

    if not robots_allows(sitemap_url):
        return FetchResult(company=company_cfg["name"], tier_used=3, source=sitemap_url,
                            ok=False, error="robots.txt disallows fetch", manual_only=True)

    try:
        resp = http_get(sitemap_url)
        if resp.status_code != 200:
            return FetchResult(company=company_cfg["name"], tier_used=3, source=sitemap_url,
                                ok=False, error=f"HTTP {resp.status_code}")
        slugs = _matching_slugs(resp.text, keywords)
    except Exception as e:
        return FetchResult(company=company_cfg["name"], tier_used=3, source=sitemap_url,
                            ok=False, error=str(e))

    jobs = []
    for url in slugs:
        m = _JOB_ID_RE.search(url)
        external_id = m.group(1) if m else url
        try:
            page = http_get(url)
            if page.status_code != 200:
                continue
            ld = _LD_JSON_RE.search(page.text)
            if not ld:
                continue
            data = json.loads(ld.group(1))
        except Exception:
            continue

        loc = data.get("jobLocation", {}).get("address", {}) if isinstance(
            data.get("jobLocation"), dict) else {}
        city = loc.get("addressLocality", "")
        region = (loc.get("addressRegion", "") or "").split(",")[0]
        location = ", ".join(p for p in (city, region, "USA") if p)

        posted = (data.get("datePosted") or "").split("T")[0]
        jobs.append({
            "company": company_cfg["display_name"],
            "title": data.get("title", ""),
            "location": location,
            "description": data.get("description", ""),
            "apply_link": url,
            "posted_date": posted,
            "external_id": external_id,
            "source_tier": 3,
        })

    return FetchResult(company=company_cfg["name"], tier_used=3, source=sitemap_url,
                        ok=True, jobs=jobs)
