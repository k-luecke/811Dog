# Session Log

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

Built export-based scraping architecture replacing paginated HTML scraping: click Export button → download CSV with all result rows including `ticketId` → fetch each detail page directly via URL template. Eliminated ExtJS grid interaction. Completed 30-day backfill for Davidson and Rutherford counties (portal hard limit is 30 days regardless of requested range). Rewrote CLI with `fetch`, `normalize`, `export-csv` commands plus `reparse-details` stub.
