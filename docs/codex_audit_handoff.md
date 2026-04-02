# TN811 Monitor — Codex Audit Handoff

**Purpose:** This document is the formal handoff from initial build to ongoing Codex-assisted maintenance. It records every deliberate decision, every known gap, every explicit assumption, and the exact procedure to validate or extend each component.

---

## Repository State at Handoff

| Component | Status | Notes |
|---|---|---|
| Config loader | ✅ Complete | `apps/jobs/src/tn811/config.py` |
| Pydantic schemas | ✅ Complete | `apps/jobs/src/tn811/models.py` |
| SQLAlchemy ORM | ✅ Complete | `apps/jobs/src/tn811/models.py` |
| DB session management | ✅ Complete | `apps/jobs/src/tn811/db.py` |
| Structured logging | ✅ Complete | `apps/jobs/src/tn811/logging.py` |
| Portal search scraper | ✅ Fixture-tested | `portal/search.py` — tested against HTML fixture, not live portal |
| Portal detail scraper | ✅ Fixture-tested | `portal/detail.py` — tested against HTML fixtures |
| Selector adapter | ✅ Complete | `portal/selectors.py` — all selectors isolated |
| Playwright browser | ✅ Complete | `portal/browser.py` |
| PDF downloader | ✅ Complete | `pdf/downloader.py` — httpx async, retry, hash |
| PDF parser chain | ✅ Complete | `pdf/parser.py` — pdfplumber → PyMuPDF → OCR |
| PDF field extractor | ✅ Tested | `pdf/extractor.py` — label-regex + positional fallback |
| Content fingerprinting | ✅ Complete | `pdf/fingerprints.py` |
| Ticket normalizer | ✅ Tested | `normalize/tickets.py` |
| Relevance matcher | ✅ Tested | `relevance/matcher.py` — fully config-driven |
| Work-group inference | ✅ Tested | `grouping/infer.py` — Jaccard similarity |
| Reminder rules | ✅ Tested | `reminders/rules.py` |
| Email templates | ✅ Tested | `reminders/templates.py` — plain + HTML |
| SMTP emailer | ✅ Complete | `reminders/emailer.py` — with dry-run |
| Dashboard JSON builder | ✅ Tested | `snapshots/build_dashboard_json.py` |
| CLI | ✅ Complete | `cli.py` — all commands implemented |
| React dashboard | ✅ Complete | `apps/dashboard/` |
| GitHub Actions | ✅ Complete | scrape, remind, publish workflows |
| Tests | ✅ ~60 test cases | See test coverage summary below |
| Sample JSON | ✅ Complete | `data/exports/*.json` |
| Docs | ✅ Complete | architecture, runbook, parser_assumptions, this doc |

---

## Explicit TODOs Remaining

### TODO-1: Live portal selector validation
**File:** `portal/selectors.py`
**Status:** Selectors are written based on the assumed portal markup described in `docs/parser_assumptions.md`. They have NOT been validated against the live TN811 portal.

**Action required before first production run:**
1. Follow the "Selector Debugging" procedure in `docs/runbook.md`.
2. Run `make scrape-dry` and confirm the county dropdown is found without errors.
3. If errors occur, update `SearchPage.COUNTY_SELECT_CANDIDATES` and related constants.
4. Update `tests/fixtures/html/search_results_davidson.html` with a real portal HTML snapshot.
5. Re-run `make test` to confirm all fixture-based tests pass.
6. Mark this TODO resolved in this document.

**Severity:** Blocker for first production run. Does not affect tests or dashboard.

---

### TODO-2: Live PDF format validation
**File:** `pdf/extractor.py`
**Status:** Regex patterns in `_LABEL_PATTERNS` are based on the assumed PDF format described in `docs/parser_assumptions.md` (Assumptions 6–8). They have NOT been validated against real TN811 PDFs.

**Action required before first production run:**
1. Download a real TN811 PDF via the portal (manual or dry-run scrape).
2. Run the text extraction: `python -c "import pdfplumber; pdf = pdfplumber.open('path/to/real.pdf'); print(pdf.pages[0].extract_text())"`
3. Compare the raw text against `_LABEL_PATTERNS`.
4. Update patterns if labels differ.
5. Create a golden test fixture in `tests/fixtures/pdf/` with a sanitized (PII-removed) version of the real PDF.
6. Add a golden test to `tests/pdf/test_extractor.py`.
7. Mark this TODO resolved.

**Severity:** High. Parse failures will occur for every ticket until resolved. The system degrades gracefully (parse failures are recorded and surfaced in the dashboard) but no structured data will be extracted.

---

### TODO-3: `bs4` dependency not in pyproject.toml
**File:** `apps/jobs/pyproject.toml`
**Status:** `BeautifulSoup` (`bs4`) is used in `portal/search.py` and `portal/detail.py` but is missing from `pyproject.toml` dependencies.

**Action required:**
Add to `pyproject.toml` `dependencies`:
```
"beautifulsoup4>=4.12",
"html5lib>=1.1",  # optional but improves BS4 parsing quality
```

**Severity:** Will cause `ImportError` on fresh install. Fix before first run.

---

### TODO-4: Postgres migration not smoke-tested
**File:** `db.py`
**Status:** The code is structured to support Postgres (swap DSN in config), but this path has not been tested.

**Action required (when Postgres migration is needed):**
1. Set `db.url` to a Postgres DSN.
2. Run `make init-db`.
3. Run `make test` with the Postgres DSN set (requires `pytest-postgresql` or a live Postgres instance).
4. Run a full `make scrape-dry` against the Postgres DB.

**Severity:** Low until Postgres migration is planned.

---

### TODO-5: `inferred_work_groups` table not populated
**File:** `models.py`, `grouping/infer.py`
**Status:** The `ORMInferredWorkGroup` ORM class and table are defined but `infer_work_groups()` does not currently write to this table. It only populates `probable_work_group` on `ORMTicket` rows.

**Action required (if cross-run group analytics are needed):**
1. In `cli.py:_run_grouping()`, after calling `infer_work_groups()`, upsert cluster summaries into `ORMInferredWorkGroup`.
2. Expose group history in `build_dashboard_json.py`.

**Severity:** Low. Current behavior (group labels on tickets) is sufficient for the dashboard.

---

### TODO-6: OCR not integration-tested
**File:** `pdf/parser.py`
**Status:** The OCR fallback path (`_parse_ocr()`) is implemented but has no integration test with a real scanned PDF. It is disabled by default.

**Action required (if scanned PDFs are encountered):**
1. Enable `pdf.ocr_enabled: true` in config.
2. Install tesseract: `apt-get install tesseract-ocr`
3. Test with a scanned PDF: `python -m tn811.cli scrape --dry-run`
4. Add an OCR fixture test if results look good.

**Severity:** Low until scanned PDFs are encountered.

---

## Test Coverage Summary

| Module | Test file | Coverage approach |
|---|---|---|
| `config.py` | `tests/test_config.py` | Unit, env var interpolation, missing file |
| `db.py` | `tests/test_db.py` | Integration with in-memory SQLite |
| `models.py` | Covered by `test_db.py` + `test_normalize.py` | ORM round-trips |
| `portal/search.py` | `tests/portal/test_search.py` | HTML fixture-based (13 cases) |
| `portal/detail.py` | `tests/portal/test_detail.py` | HTML fixture-based (15 cases) |
| `pdf/extractor.py` | `tests/pdf/test_extractor.py` | Text fixture golden tests (16 cases) |
| `normalize/tickets.py` | `tests/pdf/test_normalize.py` | Unit (18 cases) |
| `relevance/matcher.py` | `tests/relevance/test_matcher.py` | Unit (16 cases) |
| `grouping/infer.py` | `tests/grouping/test_grouping.py` | Unit (11 cases) |
| `reminders/rules.py` | `tests/reminders/test_reminders.py` | Unit (12 cases) |
| `reminders/templates.py` | `tests/reminders/test_reminders.py` | Unit, XSS check |
| `snapshots/build_dashboard_json.py` | `tests/snapshots/test_build_json.py` | Integration (9 cases) |

**Not covered by automated tests:**
- Playwright browser integration (requires live portal or recorded HAR)
- SMTP send path (requires live SMTP server)
- OCR path (requires tesseract binary)
- CLI end-to-end (requires live DB + portal)

---

## Known Fragility Points

### 1. TN811 portal markup changes
**Risk:** High. The portal's HTML structure could change without notice, breaking the scraper.
**Mitigation:** All selectors are isolated in `portal/selectors.py`. When the portal changes, only that file needs updating. The fixture-based tests will catch regressions once updated fixtures are in place.
**Detection:** The `scrape.yml` workflow will fail with a `RuntimeError` containing "Could not find county dropdown" or similar. Check GitHub Actions for failures.

### 2. TN811 PDF format changes
**Risk:** Medium. If TN811 changes the PDF template, label-regex extraction will fail.
**Mitigation:** Parse failures are recorded in `ORMParseFailure` and surfaced in the dashboard. The system degrades gracefully.
**Detection:** `parse_failures_open` count in `data/exports/summary.json` increases.

### 3. Portal rate limiting / CAPTCHA
**Risk:** Medium. TN811 may implement rate limiting or CAPTCHA if scrape volume increases.
**Mitigation:** The scraper uses respectful delays (networkidle wait), a single browser context per run, and runs only once daily. User-agent identifies the tool as an internal monitor.
**Detection:** `navigate_with_retry` will raise after all retries exhaust. The scrape workflow will fail.
**Remediation:** Reduce scrape frequency, add explicit delays in `browser.py`, or contact TN811 for API access.

### 4. GitHub Pages deployment lag
**Risk:** Low. After a scrape commits new JSON, the `publish.yml` workflow must run before the dashboard reflects new data.
**Mitigation:** `publish.yml` triggers on push to `main`, which the scrape workflow does. Typical lag is 2–5 minutes.

---

## Extending the Relevance Engine

To add a new matching rule:

1. Edit `config/monitoring.yaml` under `relevance.positive_rules` or `relevance.negative_rules`:
```yaml
- id: my_new_rule
  field: "any"           # or specific field name
  match_type: "contains" # contains | exact | regex
  pattern: "some term"
  weight: 0.60
  case_sensitive: false
```

2. Run `make scrape-dry` to see how the new rule affects scoring.
3. No code changes required — the engine reads rules entirely from config.

To add a manual override for a specific ticket:
```yaml
relevance:
  overrides:
    include:
      - "TN20240101-123456"   # force is_gfiber_related = true
    exclude:
      - "TN20240101-999999"   # force is_gfiber_related = false
```

---

## Security Checklist

- [ ] No secrets in `config/monitoring.yaml` (use `${ENV_VAR}` interpolation)
- [ ] No secrets in frontend JavaScript / dashboard artifacts
- [ ] SMTP credentials in GitHub Secrets only
- [ ] `data/raw/pdf/` and `data/raw/html/` should be in `.gitignore` if they contain PII (ticket holder names, phone numbers)
- [ ] Review whether `data/tn811.db` (which may contain PII) should be committed or `.gitignored` and managed separately

### Recommended .gitignore additions

```gitignore
# Raw portal data (may contain PII)
data/raw/
data/tn811.db
data/tn811-monitor.log
data/parsed/reminder_previews/

# Keep exports (generated, no PII beyond ticket numbers)
!data/exports/
```

---

## Dependency Pinning

Current approach: unpinned ranges in `pyproject.toml`. For production stability, pin to exact versions in a lockfile:

```bash
pip-compile apps/jobs/pyproject.toml --output-file apps/jobs/requirements.lock
```

Then in `scrape.yml`:
```yaml
- run: pip install -r apps/jobs/requirements.lock
```

---

## Handoff Checklist

Before declaring production-ready:

- [ ] TODO-1 resolved: Live portal selectors validated
- [ ] TODO-2 resolved: Live PDF format validated, golden test added
- [ ] TODO-3 fixed: `beautifulsoup4` added to `pyproject.toml`
- [ ] `.gitignore` updated for PII-containing data paths
- [ ] GitHub Secrets set: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, REMINDER_TO
- [ ] GitHub Pages enabled in repo settings
- [ ] First `make scrape-dry` run successfully (no selector errors)
- [ ] First `make remind-dry` run successfully (preview file generated)
- [ ] Dashboard loads at GitHub Pages URL with sample data
- [ ] First live scrape committed data to `data/exports/`
- [ ] Dashboard loads with real data
