"""Tier 4 — JobRight AI integration.

As of this tool's build date, JobRight AI (jobright.ai) does not expose a public,
documented API or authenticated export endpoint for third-party tools. Its product
is a closed web app; there is no free/self-serve developer API to integrate against.

This stub exists so the tier hierarchy is complete and so the pipeline logs an
honest, explicit skip rather than silently omitting Tier 4. If JobRight later
publishes an API you can authenticate into, implement the real call here and this
tier activates automatically (still excluded from the daily automated run per
config.yaml daily_run.max_tier — Tier 4 stays on-demand only).
"""
from .base import FetchResult


def fetch(company_cfg: dict) -> FetchResult:
    return FetchResult(
        company=company_cfg.get("name", "jobright"),
        tier_used=4,
        source="https://jobright.ai",
        ok=False,
        error="JobRight AI has no public API/export as of this build — skipped, not fabricated.",
        manual_only=True,
    )
