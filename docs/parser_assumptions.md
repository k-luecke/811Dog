# TN811 Monitor — Parser Assumptions

This document records every assumption the scraper and parser make about TN811 portal markup and PDF format, along with the validation procedure to verify each assumption and the remediation steps if it breaks.

---

## Portal: Search Page

### Assumption 1 — Server-rendered HTML form
**What we assume:** The search page at `https://tn811.com/ticket/search` is server-rendered HTML. The county filter is a `<select>` element and the date range is two `<input>` fields. Submitting the form navigates to a results page.

**Why we assume it:** Consistent with the 2024 TN811 portal design observed during initial build.

**Validation procedure:**
1. Open `https://tn811.com/ticket/search` in a browser with DevTools Network tab open.
2. Confirm the initial page load returns the complete HTML form (not a SPA shell that fetches via XHR).
3. Confirm the county dropdown is a `<select>` element (not a JS-rendered widget).
4. Submit the form and confirm the browser navigates to a new URL with results in the page HTML.

**Remediation if broken:**
- If the site becomes a SPA: Update `portal/search.py` to use Playwright's `page.wait_for_selector()` with the correct dynamic content selector and update `SearchPage.RESULTS_TABLE_CANDIDATES` in `portal/selectors.py`.
- If there's a REST API: Replace `SearchAdapter` with an `httpx`-based adapter that calls the API directly.

---

### Assumption 2 — Results in a single HTML `<table>`
**What we assume:** Search results appear in one HTML `<table>` element within the page. Each result is a `<tr>` in the `<tbody>`. The first `<td>` in each row contains a `<a>` link to the detail page with the ticket number as link text.

**Column order assumed (0-indexed):**
- 0: Ticket number (with link to detail page)
- 1: County
- 2: Call date
- 3: Expiration date
- 4: Excavator name
- 5: Work type
- 6: Status

**Validation procedure:**
1. Run a search and open DevTools → Elements.
2. Find the results table and confirm column ordering.
3. If columns have changed, update `SearchPage.COL_*` constants in `portal/selectors.py`.

**Remediation if broken:**
- Update column index constants in `portal/selectors.py`.
- If the table is replaced by a different structure (e.g., card grid), update `_parse_result_row()` in `portal/search.py` and add a new HTML fixture to `tests/fixtures/html/`.

---

### Assumption 3 — Pagination via a "Next" link
**What we assume:** If results span multiple pages, a link or button with text "Next" or `rel="next"` appears at the bottom of the page.

**Validation procedure:**
1. Search with a wide date window that returns many results.
2. Confirm a "Next" link appears and clicking it loads the next page of results.

**Remediation if broken:**
- Update `SearchPage.NEXT_PAGE_CANDIDATES` in `portal/selectors.py`.

---

## Portal: Detail Page

### Assumption 4 — Key-value table layout
**What we assume:** Each ticket's detail page uses a `<table>` with `<th>Label</th><td>Value</td>` rows or two-column `<td>` rows.

**Label mapping** is in `DetailPage.LABEL_TO_FIELD` in `portal/selectors.py`. Current expected labels (normalized to lowercase):
- `ticket number`, `ticket #` → `ticket_number`
- `county` → `county`
- `call date`, `called date` → `call_date`
- `legal start`, `legal start date` → `legal_start_date`
- `expiration`, `expiration date`, `expires` → `expiration_date`
- `excavator`, `excavator name`, `excavating company`, `company` → `excavator_name`
- `caller`, `caller name` → `caller_name`
- `phone`, `caller phone` → `caller_phone`
- `work type`, `type of work`, `work description` → `work_type`
- `location`, `location description`, `street address` → `location_text`
- `remarks`, `comments`, `additional information` → `remarks`

**Validation procedure:**
1. Open a ticket detail page.
2. Inspect the DOM and confirm which label text appears in `<th>` or left-column `<td>` elements.
3. Verify each label appears in `DetailPage.LABEL_TO_FIELD`.
4. Add any missing labels to the map.

**Remediation if broken:**
- Add new label → field mappings to `DetailPage.LABEL_TO_FIELD`.
- If layout is completely different (e.g., JSON in `<script>` tag), add a new extraction strategy to `portal/detail.py:parse_detail_html()`.
- Update the fixture `tests/fixtures/html/detail_TN20240601-100001.html` and run tests.

---

### Assumption 5 — One PDF link per ticket
**What we assume:** Each detail page contains exactly one link to a ticket PDF. The link either ends in `.pdf`, contains `pdf` in the href, or has text containing "PDF" or "Download".

**Validation procedure:**
1. Open a detail page.
2. Search for `<a>` tags in the HTML.
3. Confirm exactly one PDF link exists.

**Remediation if broken:**
- Update `DetailPage.PDF_LINK_CANDIDATES` in `portal/selectors.py`.
- If multiple PDFs exist, update `_find_pdf_link()` in `portal/detail.py` to select the correct one (e.g., the most recent, or by file name pattern).

---

## PDF Format

### Assumption 6 — Text-layer PDFs (not scanned images)
**What we assume:** TN811 ticket PDFs contain selectable text (not scanned images). pdfplumber can extract the text layer directly. OCR is not needed in the normal case.

**Validation procedure:**
1. Open a downloaded PDF in a PDF viewer.
2. Try to select/copy text. If you can select text, it has a text layer.
3. Run `pdfplumber` on a sample PDF and confirm non-empty output.

**Remediation if broken:**
- Enable OCR in `config/monitoring.yaml`: `pdf.ocr_enabled: true`
- Ensure tesseract is installed: `apt-get install tesseract-ocr`
- Set `pdf.tesseract_path` to the correct binary path.

---

### Assumption 7 — Labeled field format: "Label: Value"
**What we assume:** TN811 PDFs use a consistent labeled format:
```
Ticket Number: TN20240601-100001
County: Davidson
Call Date: 06/01/2024
...
```

**Current regex patterns** are in `pdf/extractor.py:_LABEL_PATTERNS`. Each field has multiple candidate patterns to handle minor label variations.

**Validation procedure:**
1. Open a downloaded PDF text dump: `python -c "import pdfplumber; pdf = pdfplumber.open('data/raw/pdf/TN20240601-100001.pdf'); print(pdf.pages[0].extract_text())"`
2. Compare the label text against `_LABEL_PATTERNS` in `pdf/extractor.py`.
3. Verify each expected field is being extracted.

**Remediation if broken:**
- Add new regex patterns to `_LABEL_PATTERNS` in `pdf/extractor.py`.
- Update golden tests in `tests/pdf/test_extractor.py` with new expected output.
- If the format is completely different (e.g., tabular, no labels), add a new extraction strategy to `extract_fields()` and register it as a new strategy.

---

### Assumption 8 — Date format: MM/DD/YYYY
**What we assume:** Dates in PDFs appear as `MM/DD/YYYY`. The parser also handles `YYYY-MM-DD` and `Month DD, YYYY` as fallbacks.

**Validation procedure:**
1. Inspect a PDF text dump for date fields.
2. Confirm format matches one of the three patterns in `parse_date()` in `pdf/extractor.py`.

**Remediation if broken:**
- Add the new date format to `_DATE_PATTERNS` in `pdf/extractor.py`.

---

## Updating This Document

When any assumption is validated against live portal data (whether it holds or was corrected), update this document with:
- Date of validation
- Portal version or scrape run ID
- Whether the assumption held or required correction
- What was changed
