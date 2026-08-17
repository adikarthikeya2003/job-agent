#!/bin/bash
# Local launchd fallback for the job-agent daily fetch, since claude.ai's cloud
# Routine is blocked on a platform-side GitHub private-repo connector bug
# (confirmed still broken 2026-08-17, first hit 2026-08-06). Runs cli.py run
# against tiers 1-3, then commits the updated dedup state (data/jobs.db) back
# to the repo so tomorrow's run only surfaces genuinely new postings, and
# drops a macOS notification with a one-line summary.

set -euo pipefail
export PATH="/opt/anaconda3/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "$(dirname "$0")/.."

LOG_DIR="logs/launchd"
mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$LOG_DIR/daily_${STAMP}.log"

echo "=== job-agent daily fetch: $(date) ===" > "$LOG_FILE"

PYTHON_BIN="/opt/anaconda3/bin/python3"

if ! "$PYTHON_BIN" cli.py run >> "$LOG_FILE" 2>&1; then
  osascript -e 'display notification "job-agent daily fetch failed — check logs/launchd/" with title "job-agent" sound name "Basso"' || true
  exit 1
fi

SUMMARY_LINE="$(grep -m1 '^Surfaced' "$LOG_FILE" || echo "Fetch complete")"

if ! git diff --quiet -- data/jobs.db; then
  git add data/jobs.db
  git commit -m "Daily fetch: $(date +%Y-%m-%d) dedup state update" >> "$LOG_FILE" 2>&1 || true
  git push origin main >> "$LOG_FILE" 2>&1 || echo "push failed, will retry next run" >> "$LOG_FILE"
fi

osascript -e "display notification \"$SUMMARY_LINE\" with title \"job-agent daily fetch\" sound name \"Glass\"" || true

echo "=== done: $(date) ===" >> "$LOG_FILE"
