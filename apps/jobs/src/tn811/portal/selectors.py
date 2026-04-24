"""
portal/selectors.py — All TN811 portal selectors in one place.

DESIGN CONTRACT:
    Every selector lives here. No selector strings appear anywhere else in the codebase.
    When the TN811 portal markup changes, this is the only file that needs updating.

ADAPTATION PROCEDURE (when selectors break):
    1. Visit https://gcv3.tn811.com/geocall/portal?directaccess=find in a browser.
    2. Run a test search for DAVIDSON county.
    3. Inspect the DOM with DevTools and update the constants below.
    4. Run: pytest tests/portal/ -v  to validate against HTML fixtures.
    5. Update tests/fixtures/ with fresh fixture data if needed.

CURRENT ASSUMPTION (as of v2):
    The TN811 public portal is an ExtJS 4/5 single-page application:
    - County selection uses an ExtJS combobox (NOT a native <select>).
      Open it by clicking the arrow trigger; items appear in a boundlist overlay.
    - Date fields are plain <input> elements (pre-filled with today on page load).
    - Search results are rendered in an ExtJS grid (virtual DOM, no static <table>).
    - The Export button triggers an immediate CSV download of all result rows.
      This replaces paginated HTML-table scraping used in v1.
    - Detail pages are plain HTML (not ExtJS) loaded via authenticated session cookie.
      URL pattern: https://gcv3.tn811.com/geocall/client/item/ticket/{ticketId}?pr=true
      The {ticketId} value comes from the export CSV's 'ticketId' column.
"""
from __future__ import annotations


# ── Search page ───────────────────────────────────────────────────────────────

class SearchPage:
    """Selectors for the ExtJS ticket search page."""

    # ExtJS combobox: click this arrow trigger to open the county dropdown
    # XPath navigates from the county input up to its trigger-wrap ancestor,
    # then down to the arrow trigger element.
    COUNTY_TRIGGER_XPATH = (
        "xpath=//input[@name='county']"
        "/ancestor::table[contains(@class,'x-form-trigger-wrap')]"
        "//div[contains(@class,'x-form-arrow-trigger')]"
    )

    # CSS for the floating boundlist items once the dropdown is open
    COUNTY_BOUNDLIST_ITEM = ".x-boundlist-item"

    # The hidden input that holds the selected county value (read-back check)
    COUNTY_INPUT = "input[name='county']"

    # Date range inputs (pre-filled with today on page load)
    DATE_FROM = "input[name='start-date']"
    DATE_TO = "input[name='end-date']"

    # Form submit — the grid-search button, NOT the nav "Find Tickets" button.
    # The nav button id (findTicketsButton-btnEl) is a different element.
    SEARCH_BUTTON = "button:has-text('Search')"

    # Export button that triggers an immediate CSV download of all result rows
    EXPORT_BUTTON = "button:has-text('Export')"

    # ExtJS grid row selector — used only to confirm results loaded before export
    GRID_ROW = "tr.x-grid-row"

    # Pager text showing total result count (e.g. "Displaying 1 - 200 of 11494")
    PAGER_TEXT = ".x-toolbar-text"


class DetailPage:
    """Selectors and constants for individual ticket detail pages."""

    # Direct URL template for a ticket detail page.
    # ticketId comes from the CSV export column 'ticketId' (NOT the Number column).
    # A valid session cookie (_nc) established by visiting the portal is required.
    URL_TEMPLATE = "https://gcv3.tn811.com/geocall/client/item/ticket/{ticket_id}?pr=true"

    # Cancellation indicator
    CANCELLED_INDICATORS = [
        ".cancelled",
        ".ticket-cancelled",
        "span:has-text('Cancelled')",
        "td:has-text('CANCELLED')",
    ]

    # Field label → canonical field name mapping.
    # Keys are normalized label text (lowercase, stripped, colon removed).
    # The detail page uses 4-column rows: (label, value, label, value).
    # Strategy 2 in parse_detail_html processes both pairs per row.
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
        "good until": "expiration_date",
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
        "work street address": "location_text",
        # Live portal Work Information section uses "Address:" for the street
        "address": "location_text",
        # Intersection is kept separate so exports can filter/sort on each
        "intersection": "intersection_text",
        # Live portal uses "Work To Begin:" not "Legal Start Date:"
        "work to begin": "legal_start_date",
        # Live portal uses "Expire Date:" not "Expiration Date:"
        "expire date": "expiration_date",
        "remarks": "remarks",
        "comments": "remarks",
        "additional information": "remarks",
        # GFiber-critical: who the work is being done for
        "done for": "done_for",
        "done for company": "done_for",
        "work done for": "done_for",
        # Geographic coordinates from the Work Information block. The portal
        # emits two point pairs per ticket — the dig path is the line segment
        # from primary to secondary, which buffers into a usable polygon for
        # spatial matching against package/permit geometry.
        "latitude": "latitude",
        "longitude": "longitude",
        "secondary lat": "secondary_latitude",
        "secondary long": "secondary_longitude",
        "work for": "done_for",
        "for company": "done_for",
    }

    # Utility response code column index within the utility table (0-based).
    # The utility table is identified by its header containing "Code" in column 0.
    UTILITY_CODE_COL = 0
