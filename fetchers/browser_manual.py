"""Tier 5 — browser automation, manual-trigger ONLY. Never called by pipeline.py's
automated daily run (enforced in pipeline.py by max_tier + explicit tier-5 exclusion,
not just by convention).

Use case: Meta (metacareers.com) is a session-gated GraphQL SPA with no stable public
API — Tier 1-3 cannot reach it reliably. This uses Playwright to drive a real
headless browser, which is:
  - Fragile: breaks on any frontend redesign, layout change, or anti-bot measure.
  - ToS risk: most large tech employer career sites' Terms of Service prohibit
    automated access/scraping. Running this against Meta or similar is a real ToS
    violation risk, not just a technical inconvenience. Only run this yourself,
    manually, at low frequency, understanding that risk — this tool will not run it
    for you automatically.

Requires (not installed by default, deliberately kept out of requirements.txt):
    pip install playwright
    playwright install chromium

Invoke explicitly:
    python3 cli.py fetch-manual --company meta --i-understand-the-tos-risk
"""
from .base import FetchResult, robots_allows

CAREERS_URL = "https://www.metacareers.com/jobs"


def fetch(company_cfg: dict, confirmed: bool = False) -> FetchResult:
    if not confirmed:
        return FetchResult(
            company=company_cfg["name"], tier_used=5, source=CAREERS_URL, ok=False,
            error="Tier 5 requires explicit confirmation "
                  "(--i-understand-the-tos-risk) — refusing to run silently.",
            manual_only=True,
        )
    if not robots_allows(CAREERS_URL):
        return FetchResult(
            company=company_cfg["name"], tier_used=5, source=CAREERS_URL, ok=False,
            error="robots.txt disallows this path — refusing even with confirmation. "
                  "This company is manual-apply-only.",
            manual_only=True,
        )
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return FetchResult(
            company=company_cfg["name"], tier_used=5, source=CAREERS_URL, ok=False,
            error="playwright not installed — run: pip install playwright && "
                  "playwright install chromium",
            manual_only=True,
        )

    jobs = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent="job-agent/1.0 manual-tier5")
            page.goto(CAREERS_URL, timeout=30000)
            page.wait_for_timeout(3000)
            # Selectors below are a best-effort starting point, not guaranteed stable —
            # inspect the live DOM (devtools) and adjust before trusting this output.
            cards = page.query_selector_all("[class*='job']")
            for card in cards[:50]:
                title_el = card.query_selector("h2, h3, [class*='title']")
                link_el = card.query_selector("a")
                if not title_el:
                    continue
                jobs.append({
                    "company": company_cfg["display_name"],
                    "title": title_el.inner_text().strip(),
                    "location": "",
                    "description": "",
                    "apply_link": link_el.get_attribute("href") if link_el else "",
                    "posted_date": "",
                    "external_id": link_el.get_attribute("href") if link_el else title_el.inner_text(),
                    "source_tier": 5,
                })
            browser.close()
    except Exception as e:
        return FetchResult(company=company_cfg["name"], tier_used=5, source=CAREERS_URL,
                            ok=False, error=str(e), manual_only=True)

    return FetchResult(company=company_cfg["name"], tier_used=5, source=CAREERS_URL,
                        ok=True, jobs=jobs, manual_only=True)
