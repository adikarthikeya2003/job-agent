#!/usr/bin/env python3
"""CLI entrypoint for job-agent.

Commands:
  run              Fetch Tiers 1-3 for all configured companies, score, dedup, output.
                    This is what cron/launchd calls for the daily automated run.
  run --company X  Same, but only for one company (on-demand).
  sync-resume      Regenerate resume.md from base_resume.tex without running a fetch.
  fetch-manual     Tier 4/5 on-demand only, never automated. Requires explicit flags.
"""
import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

import pipeline
from resume_parser import sync_resume_md
import yaml


def _link(label: str, url: str) -> str:
    """Render `label` as a clickable terminal hyperlink (OSC 8).

    Supported by iTerm2, the macOS Terminal in recent versions, VS Code and most
    modern emulators; anything that doesn't understand the escape just prints the
    label, so the raw URL is appended too and stays copy-pasteable everywhere.
    """
    if not url:
        return label
    if not sys.stdout.isatty():          # piped/redirected: keep output plain-text clean
        return f"{label}\n         {url}"
    return f"\033]8;;{url}\033\\{label}\033]8;;\033\\\n         {url}"


def _print_job(job: dict) -> None:
    head = (f"  {job['match_score']:>5} | posted {job.get('posted_date') or 'unknown'} | "
            f"exp {job.get('experience_required') or 'not stated'} | "
            f"{job['company']} | {job.get('location') or ''}")
    print(head)
    print("        " + _link(job['title'], job.get('apply_link', '')))


def cmd_run(args):
    cfg_path = BASE_DIR / "config.yaml"
    result = pipeline.run(cfg_path, daily_mode=not args.allow_tier3plus_manual,
                           company_filter=args.company, include_seen=args.include_seen)
    print(f"\nSurfaced {result['surfaced']} jobs (US-only). "
          f"Score distribution: {result['score_distribution']}")
    print(f"Outputs: {result['outputs']['csv']}")
    if result.get("html_page"):
        print(f"Clickable page ({result['html_count']} jobs): open {result['html_page']}")
    print()
    for job in result.get("top_jobs", []):
        _print_job(job)


def cmd_fetch(args):
    """User-facing 'fetch everything, live' entrypoint.

    Unlike `run` (the cron path, Tiers 1-3 only), this includes Tier 5 browser-sourced
    companies so every configured employer is represented. Browser-sourced sources are
    served from a Chrome harvest, and browser_inbox REFUSES a harvest older than its
    max_age_minutes — so a stale source fails loudly here instead of quietly returning
    yesterday's postings.
    """
    from fetchers import browser_inbox
    cfg = yaml.safe_load((BASE_DIR / "config.yaml").read_text())

    stale = []
    for company in cfg["companies"]:
        if company.get("adapter") != "browser_inbox":
            continue
        probe = browser_inbox.fetch(company)
        if not probe.ok:
            stale.append((company["name"], probe.error))

    if stale and not args.allow_stale:
        print("Cannot fetch live — these browser-sourced companies need a fresh Chrome harvest:\n")
        for name, err in stale:
            print(f"  {name}: {err}")
        print("\nAsk Claude to re-run the Chrome harvest, or pass --allow-stale to fetch "
              "the remaining sources without them.")
        sys.exit(2)

    result = pipeline.run(BASE_DIR / "config.yaml", daily_mode=False,
                          company_filter=args.company, include_seen=args.include_seen)
    print(f"\nSurfaced {result['surfaced']} jobs (US-only). "
          f"Score distribution: {result['score_distribution']}")
    print(f"Outputs: {result['outputs']['csv']}")
    if result.get("html_page"):
        print(f"Clickable page ({result['html_count']} jobs): open {result['html_page']}")
    print()
    for job in result.get("top_jobs", []):
        _print_job(job)


def cmd_sync_resume(args):
    cfg = yaml.safe_load((BASE_DIR / "config.yaml").read_text())
    tex_path = (BASE_DIR / cfg["resume"]["source_tex"]).resolve()
    md_path = (BASE_DIR / cfg["resume"]["source_md"]).resolve()
    if not tex_path.exists():
        print(f"ERROR: {tex_path} not found.")
        sys.exit(1)
    sync_resume_md(tex_path, md_path)
    print(f"Synced {md_path} from {tex_path}")


def cmd_fetch_manual(args):
    cfg = yaml.safe_load((BASE_DIR / "config.yaml").read_text())
    company_cfg = next((c for c in cfg["companies"] if c["name"] == args.company), None)
    if company_cfg is None:
        print(f"ERROR: company '{args.company}' not in config.yaml")
        sys.exit(1)
    if company_cfg["tier"] != 5 or company_cfg["adapter"] != "browser_manual":
        print(f"'{args.company}' is tier {company_cfg['tier']} — use `cli.py run --company "
              f"{args.company}` instead, fetch-manual is for Tier 5 only.")
        sys.exit(1)
    if not args.i_understand_the_tos_risk:
        print("Refusing: Tier 5 browser automation carries real ToS risk against employer "
              "career sites. Re-run with --i-understand-the-tos-risk to proceed.")
        sys.exit(1)
    from fetchers import browser_manual
    result = browser_manual.fetch(company_cfg, confirmed=True)
    if not result.ok:
        print(f"FAILED: {result.error}")
        sys.exit(1)
    print(f"Fetched {len(result.jobs)} postings from {company_cfg['display_name']} (Tier 5, manual).")
    for j in result.jobs[:20]:
        print(f"  - {j['title']} | {j['apply_link']}")


def main():
    parser = argparse.ArgumentParser(description="job-agent: free-tier job scraper + resume matcher")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Fetch + score + output (Tiers 1-3)")
    p_run.add_argument("--company", default=None, help="Only run one company by config name")
    p_run.add_argument("--allow-tier3plus-manual", action="store_true",
                        help="Internal: allow tiers beyond daily_run.max_tier (still excludes 4/5)")
    p_run.add_argument("--include-seen", action="store_true",
                        help="Also output postings already seen in a prior run (default: "
                             "only new-since-last-run). Useful for an ad-hoc 'show me today's "
                             "matches' pull after the daily job already ran once.")
    p_run.set_defaults(func=cmd_run)

    p_fetch = sub.add_parser("fetch", help="Fetch ALL companies live, including Tier-5 "
                                            "browser-sourced ones (refuses stale harvests)")
    p_fetch.add_argument("--company", default=None, help="Only fetch one company by config name")
    p_fetch.add_argument("--include-seen", action="store_true", default=True,
                          help="Include postings seen in a prior run (default on for fetch)")
    p_fetch.add_argument("--allow-stale", action="store_true",
                          help="Proceed even if a browser-sourced company has no fresh harvest")
    p_fetch.set_defaults(func=cmd_fetch)

    p_sync = sub.add_parser("sync-resume", help="Regenerate resume.md from base_resume.tex")
    p_sync.set_defaults(func=cmd_sync_resume)

    p_manual = sub.add_parser("fetch-manual", help="Tier 4/5 on-demand fetch, manual only")
    p_manual.add_argument("--company", required=True)
    p_manual.add_argument("--i-understand-the-tos-risk", action="store_true")
    p_manual.set_defaults(func=cmd_fetch_manual)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
