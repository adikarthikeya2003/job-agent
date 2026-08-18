"""Orchestrates: fetch (tier-gated) -> tag -> dedup -> score -> log -> output.

Daily automated run: only Tiers 1-3 (config.yaml daily_run.max_tier), enforced here
regardless of what an individual company's config says, so a misconfigured company
entry can never accidentally pull Tier 4/5 into cron/launchd.
"""
import logging
import re
from datetime import datetime
from pathlib import Path

import yaml

from dedup_store import DedupStore, resume_hash
from fetchers import (greenhouse, lever, rss, workday, google_careers, html_scraper,
                       jobright_stub, oracle_hcm, github_md, amazon_jobs,
                       microsoft_careers, ibm_careers, browser_inbox, detail,
                       eightfold_sitemap, ashby)
from match_scorer import MatchScorer
from resume_parser import parse_resume_sections, sync_resume_md, full_resume_text
from tagger import (tag_job, tag_experience_required, experience_min_years,
                     experience_max_years)
from text_clean import clean_description, strip_preferred_section
import output as output_mod
import outreach

BASE_DIR = Path(__file__).parent
ADAPTERS = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "rss": rss.fetch,
    "workday": workday.fetch,
    "oracle_hcm": oracle_hcm.fetch,
    "google_careers": google_careers.fetch,
    "html": html_scraper.fetch,
    "jobright": jobright_stub.fetch,
    "github_md": github_md.fetch,
    "amazon_jobs": amazon_jobs.fetch,
    "microsoft_careers": microsoft_careers.fetch,
    "ibm_careers": ibm_careers.fetch,
    "browser_inbox": browser_inbox.fetch,   # Tier 5, reads a Chrome-harvested drop file
    "eightfold_sitemap": eightfold_sitemap.fetch,
    "ashby": ashby.fetch,
    # "browser_manual" deliberately excluded — Tier 5, manual CLI path only (cli.py fetch-manual)
}


def load_config(config_path: Path) -> dict:
    return yaml.safe_load(config_path.read_text())


def setup_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"run_{stamp}.log"
    logger = logging.getLogger("job_agent")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fh = logging.FileHandler(log_file)
    sh = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh.setFormatter(fmt)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def _enrich_experience(jobs: list, cfg: dict, logger) -> None:
    """Fill in `experience_required` for surfaced postings whose source published no JD.

    Mutates the job dicts in place (the same objects are referenced by the HTML page's
    list, so both outputs pick this up). Only postings still reading "not stated" are
    fetched, and only from sources with a known detail endpoint — see fetchers/detail.py
    for why this is scoped to the surfaced set rather than the whole board.
    """
    by_name = {c["name"]: c for c in cfg["companies"]}
    pending = [j for j in jobs
               if j.get("experience_required", "not stated") == "not stated"
               and detail.supports(by_name.get(j.get("_cfg_name"), {}).get("adapter", ""))]
    if not pending:
        return

    filled = 0
    for job in pending:
        company_cfg = by_name[job["_cfg_name"]]
        jd = detail.fetch_description(job, company_cfg)
        if not jd:
            continue
        exp = tag_experience_required(job.get("title", ""), jd)
        if exp != "not stated":
            job["experience_required"] = exp
            filled += 1
    logger.info(f"Experience detail pass: {filled}/{len(pending)} surfaced postings "
                f"resolved from per-job JD fetch")


def run(config_path: Path = BASE_DIR / "config.yaml", daily_mode: bool = True,
        company_filter: str | None = None, include_seen: bool = False) -> dict:
    cfg = load_config(config_path)
    logger = setup_logging(BASE_DIR / "logs")

    resume_md_path = (BASE_DIR / cfg["resume"]["source_md"]).resolve()
    resume_tex_path = (BASE_DIR / cfg["resume"]["source_tex"]).resolve()
    if resume_tex_path.exists():
        sync_resume_md(resume_tex_path, resume_md_path)
        logger.info(f"Synced resume.md from {resume_tex_path}")

    sections = parse_resume_sections(resume_md_path)
    full_text = full_resume_text(sections)
    current_hash = resume_hash(full_text)

    scorer = MatchScorer(
        method=cfg["scoring"]["method"],
        embeddings_model=cfg["scoring"]["embeddings_model"],
        section_weights=cfg["scoring"]["section_weights"],
        score_boost=cfg["scoring"].get("score_boost", 1.0),
    )
    threshold = cfg["scoring"]["surface_threshold"]
    filters_cfg = cfg.get("filters", {})
    us_only = filters_cfg.get("us_only", True)
    max_tier = cfg["daily_run"]["max_tier"] if daily_mode else 5

    # "s?" so plural forms match too ("Directors, Data Science" must exclude on
    # "director" just like "Director of Data Science" does).
    exclude_title_terms = filters_cfg.get("exclude_title_terms", [])
    exclude_title_re = (
        re.compile(r"\b(" + "|".join(re.escape(t) for t in exclude_title_terms) + r")s?\b", re.I)
        if exclude_title_terms else None
    )
    hard_exclude_title_terms = filters_cfg.get("hard_exclude_title_terms", [])
    hard_exclude_title_re = (
        re.compile(r"\b(" + "|".join(re.escape(t) for t in hard_exclude_title_terms) + r")s?\b", re.I)
        if hard_exclude_title_terms else None
    )
    max_experience_years = filters_cfg.get("max_experience_years")
    scoring_cfg = cfg["scoring"]
    max_surfaced_jobs = scoring_cfg.get("max_surfaced_jobs")

    def _passes_seniority_filters(job: dict) -> bool:
        """Fresh-grad targeting: drop jobs requiring too much experience. A
        senior-sounding title (principal, architect, etc.) does NOT exclude a job on
        its own — some companies put "Senior" in a title for a role that's genuinely
        <4 yrs or unstated, and the user wants those surfaced (flagged, not hidden).
        A "not stated" experience_required is kept — no signal to exclude on.
        Unambiguous leadership/IC-senior titles (Director, Manager, VP, Principal,
        Head of) are hard-excluded regardless of stated experience — those titles are
        never an early-career fit even when the JD's parsed experience text is
        missing or misleading."""
        if hard_exclude_title_re and hard_exclude_title_re.search(job.get("title", "")):
            return False
        if max_experience_years is not None:
            yrs = experience_min_years(job.get("experience_required"))
            if yrs is not None and yrs >= max_experience_years:
                return False
        return True

    def _posting_age_days(job: dict) -> float | None:
        raw = job.get("posted_date")
        if not raw:
            return None
        try:
            posted = datetime.strptime(raw[:10], "%Y-%m-%d")
        except ValueError:
            return None
        return (datetime.now() - posted).total_seconds() / 86400.0

    def _apply_new_grad_bias(job: dict, base_score: float) -> float:
        """Re-rank (not re-filter) surfaced postings toward genuine new-grad fits,
        the locked primary/backup role, and freshness.
        Added 2026-08-14: "give more preference to new grad roles as I'm a new grad" and
        "quality over quantity" — a posting that clears the years filter on a technicality
        (e.g. "1-8 yrs", low end only) shouldn't outrank one that's squarely 0-2 yrs.
        Added 2026-08-17 (September Sprint): role-family and posting-age bonuses — see
        config.yaml scoring.primary_role_family / fresh_posting_bonus_* for rationale."""
        label = job.get("experience_required")
        lo = experience_min_years(label)
        hi = experience_max_years(label)
        score = base_score
        # lo==3 no longer gets a separate branch here: max_experience_years=3 (strictly
        # <3 years, tightened 2026-08-17) already drops any posting with lo>=3 before
        # this runs, so a "near-grad" 3yr posting never reaches scoring at all.
        if lo is not None and lo <= 2:
            score += scoring_cfg.get("new_grad_bonus", 0)
        if label and "grad" in label.lower():
            score += scoring_cfg.get("new_grad_label_bonus", 0)
        if job.get("senior_title_flag"):
            score -= scoring_cfg.get("senior_title_penalty", 0)
        if (max_experience_years is not None and hi is not None
                and hi > max_experience_years):
            score -= scoring_cfg.get("wide_band_penalty", 0)

        role_family = job.get("role_family")
        if role_family and role_family == scoring_cfg.get("primary_role_family"):
            score += scoring_cfg.get("primary_role_bonus", 0)
        elif role_family and role_family == scoring_cfg.get("backup_role_family"):
            score += scoring_cfg.get("backup_role_bonus", 0)

        age_days = _posting_age_days(job)
        if age_days is not None:
            if age_days <= 2:
                score += scoring_cfg.get("fresh_posting_bonus_48h", 0)
            elif age_days <= 7:
                score += scoring_cfg.get("fresh_posting_bonus_7d", 0)

        return round(max(0.0, min(score, 100.0)), 1)

    store = DedupStore(BASE_DIR / "data" / "jobs.db")

    all_jobs, score_distribution = [], []
    all_us_scored = []  # every US posting this run (any score) — feeds the clickable HTML page
    non_us_dropped = 0
    for company_cfg in cfg["companies"]:
        if company_filter and company_cfg["name"] != company_filter:
            continue
        tier = company_cfg["tier"]
        adapter_name = company_cfg["adapter"]

        if tier > max_tier:
            logger.info(f"[SKIP] {company_cfg['name']}: tier {tier} exceeds max_tier "
                        f"{max_tier} for this run — manual-only (use cli.py fetch-manual).")
            store.log_run(company_cfg["name"], tier, False, "tier exceeds max_tier for run", 0, 0)
            continue

        adapter = ADAPTERS.get(adapter_name)
        if adapter is None:
            logger.warning(f"[SKIP] {company_cfg['name']}: no adapter '{adapter_name}'")
            store.log_run(company_cfg["name"], tier, False, f"unknown adapter {adapter_name}", 0, 0)
            continue

        result = adapter(company_cfg)
        if not result.ok:
            level = logger.info if result.manual_only else logger.warning
            level(f"[{'MANUAL-ONLY' if result.manual_only else 'FAIL'}] "
                  f"{company_cfg['name']} (tier {result.tier_used}): {result.error}")
            store.log_run(company_cfg["name"], result.tier_used, False, result.error, 0, 0)
            continue

        logger.info(f"[OK] {company_cfg['name']} (tier {result.tier_used}): "
                    f"{len(result.jobs)} postings fetched")

        for job in result.jobs:
            job["description"] = clean_description(job.get("description", ""))
            tag_job(job, cfg["roles"], cfg["sponsorship"])
            # Informational only — a senior-sounding title never excludes a job on
            # its own, see _passes_seniority_filters. Surfaced so low-experience
            # "Senior"/"Staff"/etc. titles can be called out rather than hidden.
            job["senior_title_flag"] = bool(
                exclude_title_re and exclude_title_re.search(job.get("title", "")))
            # Remembered so the post-scoring detail pass knows which endpoint to call.
            job["_cfg_name"] = company_cfg["name"]

        # Score the whole company batch together so TF-IDF's IDF weighting is computed
        # across many postings, not a meaningless single resume-vs-one-JD pair. The
        # "Preferred Qualifications" section is stripped from the text used for scoring
        # only (not from job["description"], which still shows the full JD elsewhere) —
        # otherwise a resume that matches every required qualification loses points just
        # because the JD's preferred-skills section grew its vector without a match.
        jd_texts = [f"{job['title']}\n{strip_preferred_section(job['description'])}"
                    for job in result.jobs]
        score_results = scorer.score_batch(sections, jd_texts) if jd_texts else []

        new_count = 0
        for job, score_result in zip(result.jobs, score_results):
            is_new = store.is_new_or_stale(job["company"], job["external_id"], current_hash)

            job["_base_match_score"] = score_result["total"]
            job["match_score"] = _apply_new_grad_bias(job, score_result["total"])
            job["top_keywords_list"] = score_result["top_keywords"]
            job["top_keywords"] = ", ".join(score_result["top_keywords"])
            score_distribution.append(job["match_score"])

            store.upsert(job, current_hash)
            if is_new:
                new_count += 1
            if not us_only or job["is_us"]:
                all_us_scored.append(job)
            if (is_new or include_seen) and job["match_score"] >= threshold:
                if not us_only or job["is_us"]:
                    if _passes_seniority_filters(job):
                        all_jobs.append(job)
                else:
                    non_us_dropped += 1

        store.log_run(company_cfg["name"], result.tier_used, True, "", len(result.jobs), new_count)

    _enrich_experience(all_jobs, cfg, logger)
    # Re-check the experience cap now that enrichment may have resolved a "not
    # stated" job to an actual figure that clears the bar, and recompute the new-grad
    # bias off the resolved experience_required (it was scored against "not stated"
    # the first time, so it got no bonus/penalty either way).
    all_jobs = [j for j in all_jobs if _passes_seniority_filters(j)]
    for j in all_jobs:
        j["match_score"] = _apply_new_grad_bias(j, j["_base_match_score"])

    # Quality over quantity (2026-08-14): keep only the top N by (bias-adjusted) score
    # rather than dumping everything that cleared surface_threshold.
    all_jobs.sort(key=lambda j: j["match_score"], reverse=True)

    # A single employer can have a real, non-duplicate req open at a dozen offices under
    # the identical title (e.g. Capital One's 21 separately-numbered "Lead Data Engineer"
    # postings) — each one is a genuine opening, not a dedup bug, but letting all of them
    # fill a 25-slot "quality over quantity" report crowds out every other company.
    # Added 2026-08-17 after that exact case squeezed newly-added DS-focused companies
    # (Anthropic, Linear) entirely out of the discovered report despite scoring 50-60.
    MAX_PER_TITLE = 2
    seen_title_counts: dict[tuple[str, str], int] = {}
    diverse_jobs = []
    for j in all_jobs:
        key = (j.get("company", ""), j.get("title", ""))
        seen_title_counts[key] = seen_title_counts.get(key, 0) + 1
        if seen_title_counts[key] <= MAX_PER_TITLE:
            diverse_jobs.append(j)
    all_jobs = diverse_jobs

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Two reports every run, per user (2026-08-17): one scoped to companies the user
    # explicitly named, one covering every company job-agent discovered on its own
    # (crawl4ai/Firecrawl-found employers). Both are the same quality-filtered surfaced
    # set, just partitioned by config.yaml's per-company `source` tag ("user" is the
    # default for any entry that doesn't set one, i.e. every company added before this
    # partition existed). The top-N cap is applied to EACH split independently — capping
    # the combined pool first (as this used to do) let high-volume named-company boards
    # (Microsoft, IBM, etc.) crowd out every discovered-company match before the split
    # ever happened, e.g. Anthropic/Linear postings scoring 50-60 got dropped entirely
    # while the named side kept multiple near-duplicate IBM postings.
    source_by_name = {c["name"]: c.get("source", "user") for c in cfg["companies"]}
    user_jobs = [j for j in all_jobs if source_by_name.get(j["_cfg_name"], "user") == "user"]
    discovered_jobs = [j for j in all_jobs if source_by_name.get(j["_cfg_name"], "user") == "discovered"]
    if max_surfaced_jobs is not None:
        user_jobs = user_jobs[:max_surfaced_jobs]
        discovered_jobs = discovered_jobs[:max_surfaced_jobs]
    all_jobs = user_jobs + discovered_jobs
    written = output_mod.write_outputs(user_jobs, BASE_DIR / "output", stamp, suffix="_named_companies")
    written_discovered = output_mod.write_outputs(
        discovered_jobs, BASE_DIR / "output", stamp, suffix="_discovered_companies")
    logger.info(f"Named-companies report: {written['count']} jobs -> {written['csv']}")
    logger.info(f"Discovered-companies report: {written_discovered['count']} jobs -> "
                f"{written_discovered['csv']}")

    # Same two-report split as CSV/JSON/MD, as a clickable sortable page (2026-08-17,
    # "produce these as well in html just like you did for the previous one").
    html_named = output_mod.write_html(user_jobs, BASE_DIR / "output", stamp,
                                        stable_name="job_matches_named_companies.html",
                                        page_title="Job Matches — Named Companies")
    html_discovered = output_mod.write_html(discovered_jobs, BASE_DIR / "output", stamp,
                                             stable_name="job_matches_discovered_companies.html",
                                             page_title="Job Matches — Discovered Companies")
    logger.info(f"Named-companies HTML: {html_named['html']}")
    logger.info(f"Discovered-companies HTML: {html_discovered['html']}")

    outreach_result = outreach.generate_drafts(all_jobs, BASE_DIR / "output", stamp)
    logger.info(f"Outreach drafts: {outreach_result['count']} -> {outreach_result['path']}")

    html_written = output_mod.write_html(all_us_scored, BASE_DIR / "output", stamp)
    logger.info(f"Clickable page: {html_written['html']} ({html_written['count']} jobs)")

    if score_distribution:
        dist_summary = (f"n={len(score_distribution)} "
                         f"min={min(score_distribution):.1f} "
                         f"max={max(score_distribution):.1f} "
                         f"avg={sum(score_distribution)/len(score_distribution):.1f}")
    else:
        dist_summary = "n=0 (no postings scored this run)"
    logger.info(f"Score distribution: {dist_summary}")
    if us_only:
        logger.info(f"US-only filter active: dropped {non_us_dropped} non-US postings "
                    f"that cleared the score threshold")
    logger.info(f"Surfaced {written['count']} jobs >= threshold {threshold} -> {written['csv']}")

    pruned = store.prune_stale(days=45)
    if pruned:
        logger.info(f"Pruned {pruned} stale job rows (not re-seen in 45 days) to keep jobs.db small")
    store.close()
    top_jobs = sorted(all_jobs, key=lambda j: j["match_score"], reverse=True)[:15]
    return {"surfaced": written["count"] + written_discovered["count"],
            "outputs": written, "outputs_discovered": written_discovered,
            "html_named": html_named, "html_discovered": html_discovered,
            "score_distribution": dist_summary,
            "top_jobs": top_jobs, "html_page": html_written["html"],
            "html_count": html_written["count"],
            "outreach": outreach_result}
