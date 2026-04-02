# TN811 Monitor

An internal tool that monitors Tennessee 811 public ticket search results for Davidson and Rutherford Counties, identifies tickets likely related to Google Fiber / GFiber installation work, and surfaces them via a static dashboard hosted on GitHub Pages.

## What it does

1. **Scrapes** the TN811 public portal for new and changed tickets
2. **Downloads** linked PDFs and parses structured data from them
3. **Scores** tickets for relevance to GFiber/fiber-installation work
4. **Stores** all history locally in SQLite (with Postgres migration path)
5. **Generates** static JSON for a GitHub Pages dashboard
6. **Emails** batched reminders for relevant tickets expiring in 4 days

## Architecture

```
TN811 Portal → Playwright scraper → Raw HTML snapshots
                                  → PDF downloads → pdfplumber parser
                                                  → Normalized ticket records
                                                  → Relevance scoring
                                                  → SQLite history

SQLite → Dashboard JSON builder → GitHub Pages (React + Vite)
       → Reminder engine → Email (SMTP/SES)
```

## Quickstart

```bash
# Install Python deps
cd apps/jobs
pip install -e ".[dev]"
playwright install chromium

# Install frontend deps
cd ../dashboard
npm install

# Configure
cp config/monitoring.yaml.example config/monitoring.yaml
# Edit config/monitoring.yaml with your settings

# Run a dry-run scrape
make scrape-dry

# Run a dry-run reminder
make remind-dry

# Build dashboard JSON
make build-json

# Run dashboard locally
make dev-dashboard
```

## Directory Structure

```
tn811-monitor/
  apps/
    dashboard/          React + TypeScript + Vite + Tailwind
    jobs/               Python backend jobs
      src/
        config.py       Central config loader
        models.py       Pydantic schemas
        db.py           SQLAlchemy ORM + session management
        portal/         Playwright portal navigation
        pdf/            PDF download + parse pipeline
        normalize/      Ticket normalization
        relevance/      Relevance scoring engine
        grouping/       Work-group inference
        reminders/      Email reminder engine
        snapshots/      Dashboard JSON builder
        cli.py          Click CLI entry point
      tests/
  config/
    monitoring.yaml     Main config (copy from .example)
  data/
    raw/                Raw HTML snapshots and PDFs
    parsed/             Intermediate parse artifacts
    exports/            Generated dashboard JSON
  .github/workflows/    CI/CD automation
  docs/                 Architecture, runbook, handoff docs
```

## Configuration

All behavior is driven by `config/monitoring.yaml`. See `config/monitoring.yaml.example` for annotated reference.

Key sections:
- `counties`: which counties to monitor
- `date_window_days`: rolling lookback window
- `relevance`: matching rules, thresholds, overrides
- `reminders`: recipients, SMTP settings, reminder lead days
- `db`: SQLite path (swap `url` for Postgres DSN to migrate)

## Commands

```bash
make scrape            # Full scrape run
make scrape-dry        # Dry run (no writes, no PDF downloads)
make remind            # Send reminder emails
make remind-dry        # Preview reminders to disk
make build-json        # Rebuild dashboard JSON
make dev-dashboard     # Vite dev server
make build-dashboard   # Production dashboard build
make test              # Run all tests
make lint              # Ruff + mypy
```

## GitHub Actions

| Workflow | Schedule | Purpose |
|---|---|---|
| `scrape.yml` | Daily 6am CT | Scrape portal, update DB, rebuild JSON, commit |
| `remind.yml` | Daily 8am CT | Send 4-day expiry reminders |
| `publish.yml` | On push to main | Deploy dashboard to GitHub Pages |

## Secrets Required

| Secret | Used by |
|---|---|
| `SMTP_HOST` | remind workflow |
| `SMTP_PORT` | remind workflow |
| `SMTP_USER` | remind workflow |
| `SMTP_PASS` | remind workflow |
| `REMINDER_TO` | remind workflow (comma-sep email list) |

## Adding Counties

In `config/monitoring.yaml`, add an entry to `counties`:
```yaml
counties:
  - name: "Davidson County"
    state: "TN"
    portal_search_value: "Davidson"
  - name: "Williamson County"   # new
    state: "TN"
    portal_search_value: "Williamson"
```

## License

Internal tool — not for public distribution.
