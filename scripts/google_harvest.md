# Google harvest procedure (Tier 5, browser-sourced)

Google has no reachable JSON API. The old `careers.google.com/api/v3/search/` endpoint
is retired (404) and the current site posts to `batchexecute`, Google's internal RPC
protocol — reverse-engineering it means guessing RPC ids and serialized argument arrays
that break on any redesign. So Google is harvested from the rendered page instead.

This runs **on demand only** (`cli.py fetch`), never in the 08:00 cron: a browser
harvest needs Chrome open and an operator driving it.

## Procedure

For each query in {data engineer, data scientist, data analyst, machine learning
engineer} and each page 1..N:

1. Navigate to:
   `https://www.google.com/about/careers/applications/jobs/results?q=<QUERY>&location=United%20States&page=<N>`

   Pagination MUST be done by navigating to the page URL. Clicking the in-page
   "Go to next page" control updates the address bar but does not re-render the
   list — grabbing after a click returns the previous page's cards again.

2. Wait ~3.5s for the SPA to render, then inject and run `__GRAB()` + `__SAVE(tag)`
   (see `browser_harvest.js`). Each call writes one `~/Downloads/gharvest_<tag>.json`.

   The extension caps tool output at roughly 1 KB, so batches cannot be returned
   inline — that is why `__SAVE` writes a file via a Blob download instead.

3. Merge into the drop file:
   ```
   python3 scripts/build_browser_inbox.py google
   ```
   This stamps `harvested_at` with the current time, dedupes by job id, and deletes
   the consumed batch files so an old batch can never be folded into a later harvest.

4. `python3 cli.py fetch` — `browser_inbox` refuses any harvest older than
   `max_age_minutes` (90), so a stale Google source fails loudly instead of quietly
   serving yesterday's postings.

## Prerequisite: allow automatic downloads

Chrome permits ONE automatic download per site, then silently blocks the rest. Without
this setting only the first page of a harvest lands and the rest vanish with no error.

  Chrome → Settings → Privacy and security → Site settings → Additional permissions
  → Automatic downloads → Allow → add `https://www.google.com`

## Known limitations

- **No posting date.** Google's result cards do not show one, so `posted_date` is blank
  and renders as "unknown". Not fabricated.
- **Relevance-ranked, not exhaustive.** A full board pull is ~56 pages per query. The
  harvest takes the top pages per query, which is what the scorer needs anyway.
