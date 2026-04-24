# Session Log

## Known gaps / caveats (standing list — update or remove as they're fixed)

- **`scrape_runs` table is never written to.** `_run_scrape` and `_run_backfill` in `cli.py` do not insert an `ORMScrapeRun` row at start/end of a run, so there is no durable record of whether a scrape window completed successfully. This is fine for manual runs but must be wired up before any scheduled/unattended scrapes land — otherwise silent failures (e.g. a dropped county backfill window) become invisible. Fix: create an `ORMScrapeRun` at the top of each run with `status="running"`; update to `"ok"`/`"failed"` with counts at the end.
- **County field is stored with the "County" suffix.** Canonical values in `tickets.county` are `"Davidson County"` and `"Rutherford County"` — not bare `"Davidson"` / `"Rutherford"`. Any downstream filter (CSV exporter, dashboard JSON, reminder queries) must match the full string or use `LIKE 'Davidson%'`. If a display-friendly short form is needed, strip `" County"` at format time — do not change storage.
- **Local-only repo.** No `origin` remote is configured. All work is on `main` in this WSL working tree. Don't assume pushes happen; they don't.
- **`normalize_ticket` trusts detail-page ticket_number over row ticket_number.** In `normalize/tickets.py`, `ticket_number = _best_str(detail.fields.get("ticket_number"), row.ticket_number)` prefers the detail page's value. If the detail page HTML contains a cross-reference to a different ticket (seen once during `refetch-details` for `2610512744`), the normalizer produces a `NormalizedTicket` keyed to the wrong ticket and the upsert lands on the wrong DB row — leaving the original ticket's `source_html_path` NULL even after a successful fetch. Impact today is small (1 ticket out of 42). Fix: invert the precedence (row wins) or validate that `detail.fields["ticket_number"]` matches `row.ticket_number` before using it.
- **`place` (city) is never populated.** The portal CSV has a city field on `TicketRow` but it's not persisted to `ORMTicket`, so the `place` column in every master CSV is always empty. Pipeline gap, not an exporter gap.
- **`utility_summary` double-counts MWS.** Metro Water Services covers both water and sewer facilities, so `MWS` appears twice in `utility_statuses` for many tickets (e.g. `"5 clear, 3 late (GFI, MWS, MWS)"`). Not wrong per se — each is a distinct facility row in the portal's Response Status table — but the duplication reads oddly in the summary string. Decide whether to dedupe by code before next export.

## 2026-04-23 — Utility Status Extraction + Location Fix

**What was built:** Implemented full per-utility status extraction from TN811 ticket detail pages. The portal's Response Status table (4 columns: Status | Code | Name | Facilities) is now parsed into structured per-utility records with fields: `code`, `name`, `facility`, `row_status`, `last_response`, `response_timestamp`, `response_count`, `notes`, and a derived `status` enum (`clear`, `located`, `pending`, `delayed`, `not_clear`, `unknown`). A `is_late` boolean is added per utility at normalize time: `status == "pending"` and `call_date + 72h` has elapsed and ticket is not cancelled. Ticket-level rollup fields were added: `is_ready_to_dig` (all utilities clear or located), `has_late_utility`, `late_utility_codes`, `pending_utility_codes`, `blocking_utility_codes`. Fixed `location_text: None` bug by adding `"address"` → `location_text` to `LABEL_TO_FIELD` (live portal uses "Address:" not "Location:"). Also fixed `legal_start_date: None` by adding `"work to begin"` → `legal_start_date` and `"expire date"` → `expiration_date`, and added `"intersection"` → `intersection_text` as a separate field. Fixed a pre-existing `parse_date` dispatch bug where month names (e.g. "June", 4 chars) incorrectly triggered the YYYY-MM-DD branch; also added MM/DD/YY 2-digit year support for the portal's "Work To Begin" format. Added `reparse-details` CLI command to re-parse all 2,029 saved HTML snapshots against the new parser without re-fetching from the portal. Schema migration is handled inline via idempotent `ALTER TABLE ADD COLUMN` statements.

**Final DB state:** 15,923 total tickets (Davidson: 10,870, Rutherford: 5,053), 1,811 relevant-related, 2,029 with utility_statuses populated. Date range: 2026-03-25 to 2026-04-23.

**Commits this session:**
- `5e56cbb feat: utility status extraction, location fix, 2-digit year support`
- `dd38d54 fix: always migrate schema in reparse-details, even in dry-run`

**Known follow-up items:**
- MWS appears twice in `blocking_utility_codes` when it covers both water and sewer facilities — decide whether to deduplicate by code before next export
- Google Sheets export: per-utility tab (one row per utility per ticket) for filters like "show tickets where NYG is late"

## 2026-04-22 — Export-First Portal Rewrite + Full Backfill

**Context going in:** the v1 scraper built on 2026-04-21 was broken. It paginated an HTML table that the new TN811 portal (`gcv3.tn811.com/geocall/portal?directaccess=find`) doesn't render — the portal is an ExtJS app where the results grid is a virtualized `<div>` tree, not a `<table>`. The v1 code also downloaded and parsed a PDF per ticket, but the new portal has no PDF links at all. Entire pipeline needed to be replaced, not patched.

**v2 CLI rewrite (commit `d188f23`).** Stripped the PDF pipeline completely:
- `cli.py`: rewrote `_process_ticket_row` to fetch the detail page + normalize + score in one pass, no PDF download/parse. Removed `PDFDownloader`/`PDFParser` from `_run_scrape` and `_run_backfill`. Trimmed imports to just what each function actually uses.
- `normalize/tickets.py`: dropped the `extracted` / `pdf_path` / `pdf_sha256` parameters from `normalize_ticket`. Critically, changed the ticket-number regex from TN-prefix-only (`^TN\d+$`) to pure integers (`^\d{10,}$`) — the portal emits `223451234` style ticket numbers, not `TN223451234`. Kept the TN-prefix branch as a legacy fallback so old test fixtures still pass.
- `tests/pdf/test_normalize.py`: updated to the v2 two-layer signature `(row, detail)`, removed all PDF-extraction tests, added `test_valid_integer_format` to lock in the new ticket-number rule.
- `pyproject.toml`: fixed `build-backend` from the wrong `setuptools.backends.legacy:build` to `setuptools.build_meta` — was preventing `pip install -e .` from working.
- **Broken directory cleanup.** Several empty directories existed from a bash brace-expansion mishap during v1: literal directories named `{portal,pdf,...}`, `{portal,pdf,...,fixtures`, and `{portal,pdf,...,fixtures/{html,pdf}}` under `apps/jobs/src/tn811/` and `apps/jobs/tests/`. The unquoted `mkdir -p apps/jobs/src/tn811/{portal,pdf,...}` had been run under a shell where brace expansion didn't fire, creating those literal-named dirs. Deleted them.

**ExtJS selector probes (commit `5f6f291`).** Manually drove the portal in a headed Playwright session to identify the correct interaction points — the DOM is ExtJS-generated so IDs are machine-suffixed:
- **County combobox**: the visible `<input>` is wrapped in a trigger container; clicking the input itself doesn't open the list. Had to click the trigger, then select from a dynamically-mounted `.x-boundlist-item`. Boundlist values are UPPERCASE (`"DAVIDSON"`, `"RUTHERFORD"`) — updated `config/monitoring.yaml` `portal_search_value` to match.
- **Date fields**: two standard ExtJS date inputs, `startDate-inputEl` / `endDate-inputEl`, fillable with MM/DD/YYYY strings.
- **Submit button — false start.** First selector attempt was `#findTicketsButton-btnEl`. Form submitted, grid appeared, but no network activity — it was a *secondary* button that only re-filtered already-loaded rows. The actual search trigger is the toolbar's `#searchButton-btnEl`. Cost about 40 minutes of "why is the grid stale?" debugging before spotting it in the network tab.

**Export-based architecture discovery — the real unlock.** With the search firing, the grid loaded **11,494 tickets** for a Davidson 30-day window. Paginating 11.5K rows through an ExtJS virtualized grid is both slow and fragile (row recycling means DOM queries fight the renderer). Then noticed the portal's own toolbar has an `Export to CSV` button that dumps the full result set — ~5.5 MB CSV, one row per ticket, including a `ticketId` column that's the URL slug for the detail page. This collapsed the architecture:
1. Fill county/date form → click Export → `page.expect_download()` → save CSV.
2. Parse the CSV with Python `csv` module. No grid interaction, no pagination.
3. For each row worth a detail fetch, construct the detail URL directly from `DetailPage.URL_TEMPLATE.format(ticket_id=row.ticket_id)` and fetch it as plain HTML (detail pages are static, not ExtJS — use `domcontentloaded`, not `networkidle`).

**Grid / CSV column structure** (confirmed from the Davidson export fixture):
`TicketNumber, CallDate, LegalStartDate, ExpireDate, County, WorkType, ExcavatorName, WorkDoneFor, CallerName, CallerPhone, Remarks, ticketId` plus a few internal cols. `WorkDoneFor` and `Remarks` are present in the CSV, which means relevant classification can happen *without* a detail fetch for most tickets.

**Pre-filter design — `_worth_detail_fetch()`.** Detail-fetching 11,494 tickets per run would take hours. Added a row-level pre-filter in `cli.py` that inspects only CSV fields and decides whether to fetch the detail page at all. A row qualifies if any of:
- `WorkDoneFor` contains `"northstar"` or `"relevant"` (case-insensitive)
- `ExcavatorName` matches a known relevant subcontractor (North Ridge, Coastal Underground, Verity, DTX/DTX, Lakeside, Ridgeline, AMA, etc.)
- `workType` contains fiber keywords

This drops detail fetches from ~11,500 to ~50–500 per county run — a ~30× reduction. Tickets that fail the pre-filter still get a row in the DB (from CSV) with `done_for` and `remarks` populated from the CSV fallbacks, just no full detail parse.

**Portal detail page parser (`portal/detail.py`).** Detail pages use a 4-column `<td>` layout: `label | value | label | value` on each row. Strategy 2 in the parser walks pairs across the row. The utility table is identified by its `"Code"` header rather than by position. Removed all PDF-link-discovery code (`PDF_LINK_CANDIDATES`).

**Normalize fallbacks.** `normalize/tickets.py`: when a detail fetch was skipped, `done_for` and `remarks` now fall back to `row.work_done_for_raw` / `row.remarks_raw` from the CSV row, so pre-filtered tickets still have usable summary fields in the DB.

**Test suite overhaul.** Replaced 12 HTML-fixture search tests with 17 CSV-fixture tests. Added `tests/fixtures/search_results_davidson.csv` (trimmed export sample). Removed `test_pdf_url_discovered`.

**30-day dry-run results.** Davidson dry-run: 11,494 CSV rows, 1,201 passed the pre-filter. Rutherford dry-run: 5,538 CSV rows, 609 passed. Numbers looked sane — relevant work concentrated in Davidson as expected.

**Backfill results.** Portal hard-caps the search range at 30 days regardless of what's submitted (discovered by binary search — requesting 60 days silently clamped to 30). So "backfill" is a single 30-day window per county, not a chained multi-window loop. Ran for both counties:
- Davidson: 10,870 tickets stored, ~1,200 detail fetches.
- Rutherford: 5,053 tickets stored, ~610 detail fetches.
- Date range: 2026-03-25 → 2026-04-23.
- The `backfill` CLI command technically still supports chained windows for `days_back > 30` but the portal won't honor it — documented as a portal limitation rather than a code bug.

**CLI surface at end of session:** `fetch` (one county/one window), `backfill` (alias for a 30-day fetch, left in place for muscle memory), `normalize` (re-normalize existing rows from stored detail HTML — stub), `export-csv` (dashboard JSON generator — still the v1 name, to be replaced by the real CSV exporter), `reparse-details` (re-parse stored HTML snapshots, added the next day).

**Commit this session:**
- `5f6f291 feat: export-first portal rewrite + relevant pre-filter`
- `d188f23 v2 cli rewrite: remove PDF pipeline, fix ticket number validation, clean broken dirs`
