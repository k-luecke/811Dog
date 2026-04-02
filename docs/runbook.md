# TN811 Monitor — Runbook

Operational procedures for the TN811 Monitor system.

---

## Daily Operations (Automated)

| Time (CT) | Workflow | What it does |
|---|---|---|
| 6:00 AM | `scrape.yml` | Scrapes portal, downloads PDFs, scores relevance, updates DB, rebuilds JSON, commits |
| 8:00 AM | `remind.yml` | Sends expiry reminder emails for GFiber tickets expiring in 4 days |
| On push to main | `publish.yml` | Rebuilds and deploys dashboard to GitHub Pages |

---

## First-Time Setup

### 1. Clone and configure

```bash
git clone https://github.com/YOUR_ORG/tn811-monitor
cd tn811-monitor
cp config/monitoring.yaml.example config/monitoring.yaml
# Edit config/monitoring.yaml — set SMTP settings, recipients, etc.
```

### 2. Install Python dependencies

```bash
cd apps/jobs
pip install -e ".[dev]"
playwright install chromium
```

### 3. Install frontend dependencies

```bash
cd apps/dashboard
npm install
```

### 4. Initialize the database

```bash
make init-db
```

### 5. Run a dry-run scrape to verify portal connectivity

```bash
make scrape-dry
```

Review the output. If the county dropdown is not found, see [Selector Debugging](#selector-debugging) below.

### 6. Set GitHub Secrets

In your repo → Settings → Secrets and variables → Actions, set:

| Secret | Value |
|---|---|
| `SMTP_HOST` | Your SMTP server hostname |
| `SMTP_PORT` | Usually `587` |
| `SMTP_USER` | SMTP auth username |
| `SMTP_PASS` | SMTP auth password |
| `REMINDER_TO` | Comma-separated recipient email list |

### 7. Enable GitHub Pages

In repo → Settings → Pages:
- Source: GitHub Actions
- The `publish.yml` workflow will deploy on the next push to `main`.

---

## Common Operations

### Run a full scrape locally

```bash
make scrape
make build-json
```

### Preview reminder emails without sending

```bash
make remind-dry
# Preview files written to: data/parsed/reminder_previews/
```

### Send reminders (live)

```bash
make remind
```

### Rebuild dashboard JSON without scraping

```bash
make build-json
```

### View current ticket state

```bash
make list-tickets
# With filters:
python -m tn811.cli list-tickets --county Davidson --status active
```

### Run tests

```bash
make test
# With coverage:
cd apps/jobs && pytest tests/ -v --cov=src/tn811 --cov-report=term-missing
```

### Run linting

```bash
make lint
```

---

## Selector Debugging

If the scraper fails to find form elements on the TN811 portal:

### Step 1: Capture the live search page HTML

```bash
# Run with headless=false to watch the browser
python -c "
import asyncio
from tn811.config import load_config
from tn811.portal.browser import browser_context, save_page_html
from tn811.portal.browser import navigate_with_retry

async def main():
    cfg = load_config('config/monitoring.yaml')
    cfg.portal.headless = False  # Watch in browser
    async with browser_context(cfg.portal) as ctx:
        page = await ctx.new_page()
        await navigate_with_retry(page, cfg.portal.base_url + cfg.portal.search_path, cfg.portal)
        await save_page_html(page, 'data/raw/html/debug_search_page.html')
        print('Saved to data/raw/html/debug_search_page.html')
        await asyncio.sleep(5)  # pause to inspect

asyncio.run(main())
"
```

### Step 2: Inspect the saved HTML

```bash
grep -i 'county\|select\|dropdown' data/raw/html/debug_search_page.html | head -30
```

### Step 3: Update selectors

Edit `apps/jobs/src/tn811/portal/selectors.py` and update:
- `SearchPage.COUNTY_SELECT_CANDIDATES` with the correct selector
- `SearchPage.COL_*` constants if column order changed

### Step 4: Update HTML fixture and re-run tests

```bash
# Copy the live page to fixtures
cp data/raw/html/debug_search_page.html apps/jobs/tests/fixtures/html/search_results_davidson.html
# Run tests to validate your selector changes
cd apps/jobs && pytest tests/portal/ -v
```

### Step 5: Update `docs/parser_assumptions.md`

Record what changed and why.

---

## PDF Parse Failures

When tickets appear in `data/exports/parse_failures.json`:

### Step 1: Identify the failure

```bash
python -m tn811.cli list-tickets  # Won't show failures, check parse_failures.json
cat data/exports/parse_failures.json | python -m json.tool
```

### Step 2: Inspect the raw PDF

```bash
# Get the raw text from the PDF
python -c "
import pdfplumber
with pdfplumber.open('data/raw/pdf/TN20240101-XXXXXX.pdf') as pdf:
    for i, page in enumerate(pdf.pages):
        print(f'--- Page {i+1} ---')
        print(page.extract_text())
"
```

### Step 3: Check if the PDF format matches assumptions

Compare the raw text against `docs/parser_assumptions.md` Assumption 7.

### Step 4: Update extraction patterns if needed

Edit `apps/jobs/src/tn811/pdf/extractor.py` → `_LABEL_PATTERNS`.
Add a golden test to `apps/jobs/tests/pdf/test_extractor.py`.

### Step 5: Mark the failure as resolved

```bash
# After fixing, re-run the scrape to re-parse the affected ticket
make scrape
```

The parse failure will be resolved when the ticket is successfully re-parsed.

---

## Reminder Not Sending

### Check if eligible tickets exist

```bash
python -m tn811.cli list-tickets --status active
# Look for tickets with expiration_date == today + 4 days
```

### Check deduplication table

```python
# In a Python shell after init_db:
from tn811.db import get_session, init_db
from tn811.config import load_config
from tn811.models import ORMReminderEvent

cfg = load_config('config/monitoring.yaml')
init_db(cfg)

with get_session() as session:
    events = session.query(ORMReminderEvent).all()
    for e in events:
        print(e.ticket_number, e.expiration_date, e.sent_at, e.dry_run)
```

### Reset reminder deduplication (allows re-sending)

```bash
make reset-reminders
```

**Warning:** This deletes all reminder history. Only do this if you need to re-send reminders that were already sent.

### Test SMTP connectivity

```bash
python -c "
import smtplib, os
server = smtplib.SMTP(os.environ['SMTP_HOST'], int(os.environ['SMTP_PORT']), timeout=10)
server.ehlo()
server.starttls()
server.login(os.environ['SMTP_USER'], os.environ['SMTP_PASS'])
print('SMTP OK')
server.quit()
"
```

---

## Adding a New County

1. Edit `config/monitoring.yaml`:

```yaml
counties:
  - name: "Williamson County"
    state: "TN"
    portal_search_value: "Williamson"  # Must match portal dropdown exactly
    enabled: true
```

2. Verify the county dropdown value:
   - Open the TN811 portal search page in a browser.
   - Inspect the `<select>` element's `<option>` tags.
   - Use the exact `value` attribute (not display text) for `portal_search_value`.

3. Test with dry run:

```bash
python -m tn811.cli scrape --config config/monitoring.yaml --dry-run --county "Williamson"
```

4. Commit config and push.

---

## Dashboard Not Showing New Data

The dashboard reads from `data/exports/*.json`. These files are:
1. Generated by `make build-json` (or the `scrape.yml` workflow after scraping)
2. Committed to the repo by the scrape workflow
3. Copied into the dashboard's `public/` dir during `publish.yml` build

If the dashboard shows stale data:
1. Check that `scrape.yml` ran successfully (GitHub Actions → scrape workflow).
2. Check that the workflow committed changes: `git log --oneline -5`
3. Check that `publish.yml` ran after the commit.
4. Hard-refresh the browser (`Cmd+Shift+R`) to bypass CDN cache.

---

## Rotating SMTP Credentials

1. Update GitHub Secrets: `SMTP_USER`, `SMTP_PASS`
2. Test with a dry-run reminder: `make remind-dry`
3. Send a test reminder: `make remind`

---

## Disaster Recovery: DB Corruption

The SQLite database at `data/tn811.db` is committed to the repo.

If the DB is corrupted:
1. Delete `data/tn811.db`
2. Run `make init-db` to recreate the schema
3. Run `make scrape` to repopulate from the portal
4. Run `make build-json` to regenerate dashboard data

Historical snapshots will be lost, but current ticket state will be restored.

---

## Monitoring the Monitor

The scrape workflow uploads raw artifacts on every run (including failures). Check:
- GitHub Actions → scrape workflow → Artifacts → `scrape-raw-{run_id}`
- Log file: `data/tn811-monitor.log` (in the artifact zip)

Look for `"level":"ERROR"` lines in the JSON log.
