"""
portal/detail.py — TN811 ticket detail page scraper.

Fetches the detail page for a single ticket, saves raw HTML, and extracts
structured field values and utility response codes.

SELECTOR BOUNDARY: All selectors come from portal.selectors.DetailPage.

ASSUMPTION LOG:
    The detail page is plain HTML (not ExtJS) served at:
        DetailPage.URL_TEMPLATE.format(ticket_id=ticket_id)
    A valid session cookie (_nc) must already be present in the BrowserContext.
    That cookie is established automatically when the search page is visited.

    Page layout: multiple HTML tables with 4-column rows (label, value, label, value).
    Utility response codes are in a dedicated table whose first header cell is "Code".
    There are no PDF download links on the detail page.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, Page

from tn811.config import PortalConfig
from tn811.portal.browser import navigate_with_retry, save_page_html
from tn811.portal.selectors import DetailPage

logger = logging.getLogger(__name__)


@dataclass
class DetailRecord:
    """
    Structured data extracted from a ticket detail page.

    All fields are raw strings — normalization happens in normalize/tickets.py.
    """
    ticket_number: str
    county: str

    # Raw field values keyed by canonical field name (from DetailPage.LABEL_TO_FIELD)
    fields: dict[str, str] = field(default_factory=dict)

    # Utility response codes from the utility table (e.g. ["GFI", "NES"])
    utility_codes: list[str] = field(default_factory=list)

    # Always None — detail pages no longer contain PDF links
    pdf_url: str | None = None

    # True if a cancellation indicator was found on the page
    is_cancelled: bool = False

    # Full raw text of the page (for relevance fallback)
    raw_text: str = ""

    # Path where the HTML snapshot was saved
    html_snapshot_path: str | None = None


class DetailAdapter:
    """
    Adapter for TN811 individual ticket detail pages.

    Usage:
        adapter = DetailAdapter(portal_cfg, raw_html_dir)
        async with browser_context(portal_cfg) as ctx:
            record = await adapter.fetch(ctx, ticket_number, county, detail_url)
    """

    def __init__(self, portal_cfg: PortalConfig, raw_html_dir: str) -> None:
        self._cfg = portal_cfg
        self._raw_html_dir = raw_html_dir

    async def fetch(
        self,
        ctx: BrowserContext,
        ticket_number: str,
        county: str,
        detail_url: str,
    ) -> DetailRecord:
        """
        Fetch and parse a ticket detail page.

        Args:
            ctx:           Playwright BrowserContext (must have valid session cookie).
            ticket_number: Ticket identifier (for snapshot naming).
            county:        County name.
            detail_url:    Full URL to the detail page.

        Returns:
            DetailRecord with extracted fields and utility codes.
        """
        page: Page = await ctx.new_page()
        try:
            logger.info("Fetching detail page", extra={"ticket": ticket_number, "url": detail_url})
            # Plain HTML page — domcontentloaded is sufficient and faster than networkidle
            await navigate_with_retry(page, detail_url, self._cfg, wait_until="domcontentloaded")

            html = await page.content()
            snapshot_path = self._snapshot_path(ticket_number)
            await save_page_html(page, snapshot_path)

            record = parse_detail_html(html, ticket_number, county, self._cfg.base_url)
            record.html_snapshot_path = snapshot_path
            return record
        finally:
            await page.close()

    def _snapshot_path(self, ticket_number: str) -> str:
        safe_num = ticket_number.replace("/", "_").replace("\\", "_")
        return f"{self._raw_html_dir}/detail_{safe_num}.html"


def parse_detail_html(
    html: str,
    ticket_number: str,
    county: str,
    base_url: str,
) -> DetailRecord:
    """
    Parse a ticket detail page HTML string into a DetailRecord.

    This function is pure (no I/O) and tested against HTML fixtures.

    Parsing strategy:
    1. Look for <th>Label</th><td>Value</td> rows (legacy format compat).
    2. Look for 4-column <td> rows: (label, value, label, value).
       Falls back to 2-column (label, value) if only 2 TDs present.
    3. Definition lists <dl><dt>Label</dt><dd>Value</dd></dl>.
    4. Utility table: find the table whose first header cell is "Code";
       extract the code from column 0 of each data row.
    5. Cancellation: scan for "cancelled"/"canceled"/"void" in prominent elements.
    """
    soup = BeautifulSoup(html, "html.parser")
    record = DetailRecord(ticket_number=ticket_number, county=county)
    record.raw_text = soup.get_text(separator=" ", strip=True)

    # ── Extract key-value fields ────────────────────────────────────────────
    fields: dict[str, str] = {}

    # Strategy 1: <th>Label</th><td>Value</td> in the same row
    for row in soup.find_all("tr"):
        header = row.find("th")
        data = row.find("td")
        if header and data:
            label = header.get_text(strip=True).lower().rstrip(":")
            value = data.get_text(strip=True)
            canonical = DetailPage.LABEL_TO_FIELD.get(label)
            if canonical:
                fields[canonical] = value

    # Strategy 2: td-only rows — 4-column (label,value,label,value) or 2-column
    if not fields:
        for row in soup.find_all("tr"):
            tds = row.find_all("td")
            if len(tds) == 4:
                pairs = [(tds[0], tds[1]), (tds[2], tds[3])]
            elif len(tds) >= 2:
                pairs = [(tds[0], tds[1])]
            else:
                continue
            for label_td, value_td in pairs:
                label = label_td.get_text(strip=True).lower().rstrip(":")
                value = value_td.get_text(strip=True)
                canonical = DetailPage.LABEL_TO_FIELD.get(label)
                if canonical:
                    fields[canonical] = value

    # Strategy 3: Definition lists <dl><dt>Label</dt><dd>Value</dd></dl>
    for dt in soup.find_all("dt"):
        label = dt.get_text(strip=True).lower().rstrip(":")
        canonical = DetailPage.LABEL_TO_FIELD.get(label)
        if canonical:
            dd = dt.find_next_sibling("dd")
            if dd:
                fields[canonical] = dd.get_text(strip=True)

    record.fields = fields

    # ── Utility codes (GFI etc.) from utility response table ────────────────
    record.utility_codes = _extract_utility_codes(soup)

    # ── Cancellation ────────────────────────────────────────────────────────
    cancelled_text_signals = ["cancelled", "canceled", "void"]
    page_text_lower = record.raw_text.lower()
    for sig in cancelled_text_signals:
        if sig in page_text_lower:
            for el in soup.find_all(["h1", "h2", "h3", "span", "div", "td"]):
                if sig in el.get_text(strip=True).lower():
                    record.is_cancelled = True
                    break
        if record.is_cancelled:
            break

    logger.debug(
        "Parsed detail page",
        extra={
            "ticket": ticket_number,
            "fields_found": len(fields),
            "utility_codes": record.utility_codes,
            "cancelled": record.is_cancelled,
        },
    )
    return record


def _extract_utility_codes(soup: BeautifulSoup) -> list[str]:
    """
    Extract utility response codes from the detail page.

    The utility table is identified by its header row having "code" as the
    first column header (case-insensitive). Codes are taken from column 0
    of all subsequent data rows.

    Falls back to regex scan of the raw page text if no such table is found.
    """
    codes: list[str] = []
    seen: set[str] = set()

    def _add(code: str) -> None:
        c = code.strip().upper()
        if c and c not in seen:
            codes.append(c)
            seen.add(c)

    # Strategy 1: table whose first header cell is "Code"
    for table in soup.find_all("table"):
        header_row = table.find("tr")
        if not header_row:
            continue
        header_cells = header_row.find_all(["th", "td"])
        if not header_cells:
            continue
        first_header = header_cells[0].get_text(strip=True).lower()
        if first_header != "code":
            continue
        # This is the utility table — extract code from col 0 of each data row
        all_rows = table.find_all("tr")
        for tr in all_rows[1:]:  # skip header row
            cells = tr.find_all(["td", "th"])
            if cells:
                code_text = cells[DetailPage.UTILITY_CODE_COL].get_text(strip=True)
                if code_text:
                    _add(code_text)
        if codes:
            return codes

    # Strategy 2: regex scan for short uppercase codes near utility context
    import re as _re
    raw = soup.get_text(separator="\n")
    for m in _re.finditer(
        r"\b([A-Z]{2,8})\b(?=\s*[-:|\s]?\s*(?:utility|notified|response|locate|marked))",
        raw,
        _re.IGNORECASE,
    ):
        _add(m.group(1))

    return codes
