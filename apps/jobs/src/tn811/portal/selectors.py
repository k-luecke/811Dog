"""
portal/selectors.py — All TN811 portal selectors in one place.

DESIGN CONTRACT:
    Every selector lives here. No selector strings appear anywhere else in the codebase.
    When the TN811 portal markup changes, this is the only file that needs updating.

ADAPTATION PROCEDURE (when selectors break):
    1. Visit https://tn811.com/ticket/search in a browser.
    2. Run a test search for Davidson County.
    3. Inspect the DOM with DevTools and update the constants below.
    4. Run: pytest tests/portal/ -v  to validate against HTML fixtures.
    5. Update tests/fixtures/html/ with fresh portal HTML if needed.
    6. Update docs/parser_assumptions.md with the new selector rationale.

CURRENT ASSUMPTION (as of initial build):
    The TN811 public portal is a server-rendered HTML form with:
    - A county dropdown (<select>) identified by name/id
    - A date-range input pair
    - A results table with ticket rows
    - Each ticket row links to a detail page
    - Detail pages contain a PDF download link

    These assumptions are encoded as an adapter interface. If the portal
    uses a different mechanism (e.g., JavaScript-rendered SPA, AJAX endpoints),
    update the SearchAdapter and DetailAdapter implementations in
    portal/search.py and portal/detail.py accordingly — the selector constants
    here would then contain API endpoint paths or JSON field names instead.
"""
from __future__ import annotations


# ── Search page ───────────────────────────────────────────────────────────────

class SearchPage:
    """Selectors for the ticket search/results page."""

    # County dropdown — try multiple candidate selectors; first match wins
    # Validation: ensure the selected option value matches the county portal_search_value
    COUNTY_SELECT_CANDIDATES = [
        "select[name='county']",
        "select[id='county']",
        "select[name='County']",
        "#county-select",
        "select.county-dropdown",
    ]

    # Date range inputs
    # Assumption: the portal accepts MM/DD/YYYY format
    DATE_FROM_CANDIDATES = [
        "input[name='startDate']",
        "input[name='start_date']",
        "input[name='dateFrom']",
        "input[id='startDate']",
        "#date-from",
    ]
    DATE_TO_CANDIDATES = [
        "input[name='endDate']",
        "input[name='end_date']",
        "input[name='dateTo']",
        "input[id='endDate']",
        "#date-to",
    ]

    # Search submit button
    SUBMIT_CANDIDATES = [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Search')",
        "button:has-text('Submit')",
        "#search-btn",
    ]

    # Results container — the table or list of ticket rows
    RESULTS_TABLE_CANDIDATES = [
        "table.results",
        "table#results",
        "#ticket-results table",
        ".ticket-list table",
        "table",  # last-resort fallback
    ]

    # Each result row in the results table (skip header row)
    RESULT_ROW = "tbody tr"

    # Within a result row:
    # Ticket number link (also the detail page link)
    ROW_TICKET_LINK = "a"

    # Column indices (0-based) — update if column order changes
    # These are used as fallback when cell data-attributes are absent
    COL_TICKET_NUMBER = 0
    COL_COUNTY = 1
    COL_CALL_DATE = 2
    COL_EXPIRATION_DATE = 3
    COL_EXCAVATOR = 4
    COL_WORK_TYPE = 5
    COL_STATUS = 6

    # Pagination — next page link/button
    NEXT_PAGE_CANDIDATES = [
        "a[rel='next']",
        "a:has-text('Next')",
        ".pagination .next a",
        "button:has-text('Next Page')",
    ]

    # "No results" indicator
    NO_RESULTS_CANDIDATES = [
        ".no-results",
        "td:has-text('No tickets found')",
        "td:has-text('No results')",
        "p:has-text('No tickets')",
    ]


class DetailPage:
    """Selectors for the individual ticket detail page."""

    # Key-value pairs on the detail page
    # Strategy: look for <th>Label</th><td>Value</td> patterns
    # or definition lists <dt>Label</dt><dd>Value</dd>
    DETAIL_TABLE_CANDIDATES = [
        "table.ticket-detail",
        "table#ticket-detail",
        ".detail-table",
        "table",
    ]

    # PDF download link
    PDF_LINK_CANDIDATES = [
        "a[href$='.pdf']",
        "a:has-text('PDF')",
        "a:has-text('Download')",
        "a[href*='pdf']",
        "a[href*='document']",
    ]

    # Cancellation indicator
    CANCELLED_INDICATORS = [
        ".cancelled",
        ".ticket-cancelled",
        "span:has-text('Cancelled')",
        "td:has-text('CANCELLED')",
    ]

    # Field label → canonical field name mapping
    # Keys are normalized (lowercase, stripped) label text from the portal
    # Values are NormalizedTicket field names
    # Update this map when the portal's field labels change
    LABEL_TO_FIELD: dict[str, str] = {
        "ticket number": "ticket_number",
        "ticket #": "ticket_number",
        "county": "county",
        "call date": "call_date",
        "called date": "call_date",
        "legal start": "legal_start_date",
        "legal start date": "legal_start_date",
        "expiration": "expiration_date",
        "expiration date": "expiration_date",
        "expires": "expiration_date",
        "excavator": "excavator_name",
        "excavator name": "excavator_name",
        "excavating company": "excavator_name",
        "company": "excavator_name",
        "caller": "caller_name",
        "caller name": "caller_name",
        "phone": "caller_phone",
        "caller phone": "caller_phone",
        "work type": "work_type",
        "type of work": "work_type",
        "work description": "work_type",
        "location": "location_text",
        "location description": "location_text",
        "street address": "location_text",
        "remarks": "remarks",
        "comments": "remarks",
        "additional information": "remarks",
        # GFiber-critical: who the work is being done for
        "done for": "done_for",
        "done for company": "done_for",
        "work done for": "done_for",
        "work for": "done_for",
        "for company": "done_for",
    }

    # Selectors for the utility response table (contains codes like GFI)
    UTILITY_TABLE_CANDIDATES = [
        "table.utility-table",
        "table#utilities",
        "table.utilities",
        "#utility-responses table",
        ".utility-list table",
    ]

    # Within utility rows: code column index (0-based)
    UTILITY_CODE_COL = 0
    UTILITY_NAME_COL = 1
