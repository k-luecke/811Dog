# TN811 Monitor — Architecture

## Overview

TN811 Monitor is a Python + React system that monitors the Tennessee 811 public ticket portal for excavation tickets likely related to Northstar Fiber / relevant installation work, and surfaces them via a static dashboard hosted on GitHub Pages.

## Component Map

```
┌──────────────────────────────────────────────────────────────────┐
│  TN811 Public Portal (https://tn811.com)                         │
└───────────────────────────┬──────────────────────────────────────┘
                            │ Playwright (headless Chromium)
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│  portal/search.py        — County + date search, result rows     │
│  portal/detail.py        — Per-ticket detail page scraping       │
│  portal/selectors.py     — ALL CSS selectors (single file)       │
│  portal/browser.py       — Playwright lifecycle + retry          │
└───────────────────────────┬──────────────────────────────────────┘
                            │ Raw HTML snapshots → data/raw/html/
                            │ PDF URLs discovered
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│  pdf/downloader.py       — httpx async download, SHA-256 hash    │
│  pdf/parser.py           — pdfplumber → PyMuPDF → OCR chain      │
│  pdf/extractor.py        — Label-regex + positional field extract │
│  pdf/fingerprints.py     — Hash-based duplicate detection        │
└───────────────────────────┬──────────────────────────────────────┘
                            │ ExtractedFields
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│  normalize/tickets.py    — Merge row + detail + PDF; NormTicket  │
│  relevance/matcher.py    — Weighted rule scoring (0.0–1.0)       │
│  grouping/infer.py       — Jaccard-similarity work-group cluster  │
└───────────────────────────┬──────────────────────────────────────┘
                            │ NormalizedTicket
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│  db.py / models.py       — SQLAlchemy ORM, SQLite (→ Postgres)   │
│  ORMTicket               — Current state                         │
│  ORMTicketSnapshot       — Immutable history (content-hash keyed)│
│  ORMScrapeRun            — Operational audit                      │
│  ORMParseFailure         — Failures for review                    │
└────────────┬───────────────────────────────────┬─────────────────┘
             │                                   │
             ▼                                   ▼
┌────────────────────────┐          ┌────────────────────────────┐
│ snapshots/             │          │ reminders/                  │
│ build_dashboard_json   │          │ rules.py — eligibility      │
│ → data/exports/*.json  │          │ emailer.py — SMTP / preview │
└────────────┬───────────┘          │ templates.py — HTML + text  │
             │                      └────────────────────────────┘
             ▼
┌──────────────────────────────────────────────────────────────────┐
│  apps/dashboard/         — React + TypeScript + Vite + Tailwind  │
│  Reads ONLY from data/exports/*.json (no live portal calls)      │
│  Deployed to GitHub Pages via publish.yml                        │
└──────────────────────────────────────────────────────────────────┘
```

## Data Flow: Ticket Lifecycle

```
New ticket discovered in portal search
  └─→ detail page fetched → HTML snapshot saved
        └─→ PDF URL discovered → PDF downloaded (SHA-256 hashed)
              └─→ PDF parsed (pdfplumber → PyMuPDF → OCR)
                    └─→ Fields extracted
                          └─→ Normalized ticket assembled
                                └─→ Relevance scored (0.0–1.0)
                                      └─→ Work group inferred
                                            └─→ Stored as ORMTicket
                                                  └─→ ORMTicketSnapshot (if new/changed)

Changed ticket (same ticket_number, different content_hash)
  └─→ New ORMTicketSnapshot appended (history preserved)
  └─→ ORMTicket.updated_at bumped

Unchanged ticket (same content_hash)
  └─→ No write; stats.unchanged++
```

## Key Design Decisions

### 1. Local truth after capture
Once a PDF is downloaded and hashed, it is the canonical snapshot for that version. The portal is only consulted again to discover new/changed/cancelled tickets and fetch missing PDFs. This minimizes portal load and makes the system resilient to portal downtime.

### 2. Content hashing for change detection
`NormalizedTicket.content_hash()` computes a SHA-256 of the stable field payload (excluding runtime metadata like `parsed_at`, `relevance_score`). A new `ORMTicketSnapshot` is appended only when this hash changes, providing a full audit trail without storing duplicates.

### 3. Selector isolation
All TN811 portal CSS selectors live exclusively in `portal/selectors.py`. When the portal's markup changes, only that file needs updating. The search and detail adapters consume selectors but never hardcode them.

### 4. Relevance engine
The relevance engine is fully rule-based and config-driven (`config/monitoring.yaml → relevance`). It does not use a hardcoded subcontractor list. Rules are weighted, support regex, and include manual include/exclude overrides per ticket number. Adding new rules requires only a YAML edit.

### 5. Static dashboard
The React dashboard reads only from pre-generated JSON files. It makes no live calls to TN811 or the database at render time. This makes the dashboard:
- Fast (no API latency)
- Secure (no secrets in frontend)
- Deployable to GitHub Pages with zero backend
- Auditable (JSON files are version-controlled)

### 6. Work-group inference (not subcontractor tracking)
Work groups are inferred using Jaccard similarity on company name + work type tokens. This clusters tickets from the same apparent crew without requiring a hardcoded company list. The threshold is configurable.

### 7. Public/private data boundary
The public TN811 pipeline is the product core: scrape, normalize, score, group, store, and export must continue to work with public records only. Customer-owned data belongs behind explicit integration interfaces and must enrich public tickets without mutating the public source record.

The boundary is represented in code by:

| Module | Purpose |
|---|---|
| `data_boundary.py` | Data classifications, domains, export policies, tenant context |
| `integrations/interfaces.py` | Provider/enricher protocols for customer private data |

Allowed data classes:

| Classification | Meaning | Public export allowed |
|---|---|---|
| `public` | Public source records, such as TN811 or public GIS | Yes |
| `derived` | Scores, groupings, or analytics derived from allowed inputs | Yes |
| `customer_private` | Customer projects, assets, work orders, CRM, crews | No |
| `restricted` | Contract-sensitive, regulated, or specially controlled data | No |

Private data should be joined after normalization through a customer-scoped enricher. Exporters must validate classified values against an explicit `ExportPolicy` before writing data outside the tenant boundary.

## Database Schema

See `models.py` for the full ORM definition. Key tables:

| Table | Purpose |
|---|---|
| `tickets` | One row per unique ticket_number; current state |
| `ticket_snapshots` | Immutable payload per content hash; full history |
| `reminder_events` | Sent reminders; prevents duplicate sends |
| `scrape_runs` | Operational audit of each scrape job |
| `parse_failures` | Failures for manual review |
| `inferred_work_groups` | Cluster metadata (optional, for future analytics) |

## Adding a New County

1. Add entry to `config/monitoring.yaml` under `counties:`
2. Set `enabled: true`
3. Set `portal_search_value` to the exact string the TN811 portal dropdown expects
4. Run `make scrape-dry` to verify the county search works
5. Commit config change

## Migrating to Postgres

Change `db.url` in `config/monitoring.yaml`:
```yaml
db:
  url: "postgresql+psycopg2://user:pass@host:5432/tn811"
```
Run `make init-db` to create tables. No code changes required.
