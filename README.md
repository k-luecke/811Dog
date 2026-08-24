# TN811 Monitor

A monitoring pipeline for Tennessee 811 excavation tickets. It scrapes the
public TN811 portal, parses the linked PDFs, scores each ticket against
configurable relevance rules, keeps the full history in SQLite, and publishes a
static dashboard plus expiry reminder emails.

Built for a specific job — tracking fiber-installation work across two Middle
Tennessee counties — but nothing about the target is hard-coded. Which counties,
which contractors, and which work types matter all live in
`config/monitoring.yaml`.

## What it does

```
TN811 portal ──Playwright──▶ raw HTML snapshots
                          └▶ PDF downloads ──pdfplumber──▶ normalized tickets
                                                        ──▶ relevance scoring
                                                        ──▶ SQLite history

SQLite ──▶ dashboard JSON ──▶ GitHub Pages (React + Vite)
       ──▶ reminder engine ──▶ email (SMTP)
       ──▶ CSV / KMZ exports
```

## Design notes

A few decisions worth calling out, since they're the parts that took real work:

**Export-first scraping.** The portal's search grid can be exported as CSV,
which carries `WorkDoneFor`, `ExcavatorName`, and `Remarks`. That means most
tickets can be classified without opening a detail page at all. Only rows that
pass a cheap pre-filter (`_worth_detail_fetch`) get the expensive fetch —
tens of thousands of requests per run become hundreds.

**The pre-filter errs toward fetching.** A missed detail page means a missed
ticket; an extra fetch costs one request. So the filter is deliberately loose,
and every signal it uses comes from config.

**Two-sided contractor rule.** A ticket also counts as relevant when a tracked
contractor is doing the tracked *kind* of work — both halves required. A known
contractor laying water pipe is not a hit, and an unknown contractor pulling
fiber is already covered by the weighted rules. This catches subcontractors who
file terse tickets naming neither the client nor the technology.

**Scoring is explainable.** Every ticket carries `relevance_reasons` listing
exactly which rules fired and at what weight, so a surprising classification can
be traced to a rule rather than argued about.

**Rebuildable classifications.** `tn811 rescore` re-runs current rules over
every ticket already stored, so a rule change surfaces existing tickets without
re-scraping the portal.

## Configuration

Everything behavioral lives in `config/monitoring.yaml`. Copy the annotated
reference and edit:

```bash
cp config/monitoring.yaml.example config/monitoring.yaml
```

The example is tuned for fiber work as an illustration. The engine itself knows
nothing about any company or industry — replace the patterns with your own.

| Section | Controls |
|---|---|
| `counties` | Which counties to search, and their portal dropdown values |
| `relevance.positive_rules` | Weighted match rules (`contains` / `exact` / `regex`) |
| `relevance.negative_rules` | Rules that subtract from the score |
| `relevance.contractor_rule` | The two-sided contractor + work-type signal |
| `relevance.detail_fetch_*` | Scrape-time pre-filter signals |
| `relevance.overrides` | Per-ticket include/exclude, beats scoring |
| `reminders` | Lead days, recipients, SMTP settings |
| `grouping` | Work-group clustering threshold and fields |

`config/monitoring.yaml` is gitignored — it holds your own rules, and they are
usually not something you want in a public repo.

## Quickstart

Requires Python 3.12+ and Node 18+.

```bash
# Backend
cd apps/jobs
pip install -e ".[dev]"
playwright install chromium

# Frontend
cd ../dashboard && npm install

# Configure
cp config/monitoring.yaml.example config/monitoring.yaml

# Dry run — no writes, no PDF downloads
make scrape-dry
```

## Commands

```bash
make scrape            # Full scrape run
make scrape-dry        # Dry run
make remind            # Send reminder emails
make remind-dry        # Render reminders to disk instead of sending
make build-json        # Rebuild dashboard JSON
make dev-dashboard     # Vite dev server
make build-dashboard   # Production dashboard build
make test              # pytest
make lint              # Ruff + mypy
```

## Tests

```bash
cd apps/jobs && pytest
```

335 tests covering PDF parsing, ticket normalization, relevance scoring,
grouping, reminder rules and rendering, CSV/KMZ export, the data boundary, and
the portal adapters. Portal and PDF behavior is tested against captured
fixtures, so the suite runs offline with no network access.

## Scheduled workflows

| Workflow | Schedule | Purpose |
|---|---|---|
| `scrape.yml` | Daily 06:00 CT | Scrape, update DB, rebuild JSON, commit |
| `remind.yml` | Daily 08:00 CT | Send expiry reminders |
| `publish.yml` | Push to `main` | Deploy dashboard to GitHub Pages |

Reminder secrets (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`,
`REMINDER_TO`) are repository secrets, used only by `remind.yml`.

## Data handling

Ticket records are public records, but they contain caller names and phone
numbers. `data/raw/` and `data/parsed/` are gitignored for that reason, and
`data_boundary.py` defines which fields may cross into the published dashboard
JSON — the dashboard is a static site, so anything in that JSON is public.

## License

See [LICENSE](LICENSE).
