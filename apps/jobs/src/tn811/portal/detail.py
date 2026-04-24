"""
portal/detail.py — TN811 ticket detail page scraper.

Fetches the detail page for a single ticket, saves raw HTML, and extracts
structured field values, utility response codes, and per-utility status records.

SELECTOR BOUNDARY: All selectors come from portal.selectors.DetailPage.

ASSUMPTION LOG:
    The detail page is plain HTML (not ExtJS) served at:
        DetailPage.URL_TEMPLATE.format(ticket_id=ticket_id)
    A valid session cookie (_nc) must already be present in the BrowserContext.
    That cookie is established automatically when the search page is visited.

    Page layout:
    - generalInformation div: ticket header (4-col rows) + Response Status table
    - excavatorInfo div: excavator details (4-col rows)
    - workInfo div: work location (separate 2-col and 4-col tables)
    - memeberInfo div (sic): Utilities Notified table (Code | Name | Added Manually?)

    Response Status table columns: Status | Code | Name | Facilities
      Row status: "Open" (no utility response yet) or "Closed" (responded)
      Each utility row may contain a <ul> of <li> response history entries.
      The last <li> is the current state; response count = total <li> elements.

    Utility status enum (derived from row_status + last response text):
      clear    — no facilities or no conflict; safe to dig
      located  — facilities exist and have been marked; dig carefully
      pending  — utility has not yet responded (Open, no <li> entries)
      delayed  — locate was delayed (Open or Closed)
      not_clear — in conflict, cannot locate, or closed without response
      unknown  — unrecognized combination
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, Page

from tn811.config import PortalConfig
from tn811.portal.browser import navigate_with_retry, save_page_html
from tn811.portal.selectors import DetailPage

logger = logging.getLogger(__name__)


# Maps (row_status.lower(), last_response_text.lower()) → util_status enum string.
# Keys with response=None represent Open rows with zero <li> entries (no response yet).
_UTIL_STATUS_MAP: dict[tuple[str, str | None], str] = {
    ("open", None): "pending",
    ("open", "locate delayed"): "delayed",
    ("open", "cannot locate - contact utility"): "not_clear",
    ("open", "clear - no conflict"): "clear",
    ("closed", "clear - no conflict"): "clear",
    ("closed", "no marking needed"): "clear",
    ("closed", "utility generated ticket - self locate"): "clear",
    ("closed", "located to meter only"): "clear",
    ("closed", "located - facilities marked"): "located",
    ("closed", "locate delayed"): "delayed",
    ("closed", "in conflict"): "not_clear",
    ("closed", "no response"): "not_clear",
}

# Trailing whitespace appears inside the portal's span text; normalise on lookup.
_RESPONSE_STRIP_RE = re.compile(r"\s+")


def _derive_util_status(row_status: str, last_response: str | None) -> str:
    """Map (row_status, last_response) to a util_status enum string."""
    rs = row_status.strip().lower()
    lr = _RESPONSE_STRIP_RE.sub(" ", last_response).strip().lower() if last_response else None
    return _UTIL_STATUS_MAP.get((rs, lr), "unknown")


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

    # Utility response codes from the Utilities Notified table (e.g. ["GFI", "NES"])
    utility_codes: list[str] = field(default_factory=list)

    # Per-utility status records from the Response Status table.
    # is_late is NOT set here — it requires call_date and is computed in normalize.
    # Shape per record: {code, name, facility, row_status, last_response,
    #                    response_timestamp, response_count, notes, status}
    utility_statuses: list[dict[str, Any]] = field(default_factory=list)

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
            DetailRecord with extracted fields, utility codes, and utility statuses.
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
    4. Utility codes: Utilities Notified table (first header cell == "Code").
    5. Utility statuses: Response Status table (inside generalInformation div
       with h3 starting "Response Status").
    6. Cancellation: scan for "cancelled"/"canceled"/"void" in prominent elements.
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

    # ── Utility statuses from Response Status table ─────────────────────────
    record.utility_statuses = _extract_utility_statuses(soup)

    # ── Utility codes from Utilities Notified table ─────────────────────────
    # Keep this thin wrapper for backward compatibility with relevance matching.
    record.utility_codes = _extract_utility_codes(soup, record.utility_statuses)

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
            "utility_statuses": len(record.utility_statuses),
            "cancelled": record.is_cancelled,
        },
    )
    return record


def _extract_utility_statuses(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """
    Extract per-utility status records from the Response Status table.

    The table lives inside <div class="generalInformation"> whose <h3> text
    starts with "Response Status". It has four columns:
      Status | Code | Name | Facilities

    Each data row's <td class="name"> contains a <ul> of <li> response history
    entries. The *last* <li> is the current state. response_count is the total
    number of <li> elements (non-zero = utility has interacted with the ticket).

    Returns:
        List of dicts, one per utility row. is_late is NOT set here.
    """
    records: list[dict[str, Any]] = []

    for div in soup.find_all("div", class_="generalInformation"):
        h3 = div.find("h3")
        if not h3 or not h3.get_text(strip=True).startswith("Response Status"):
            continue

        table = div.find("table")
        if not table:
            continue

        for row in table.find_all("tr"):
            tds = row.find_all("td")
            if len(tds) < 4:
                continue

            row_status = tds[0].get_text(strip=True)
            code = tds[1].get_text(strip=True).upper()
            name_td = tds[2]
            facility = tds[3].get_text(strip=True)

            # Extract utility display name (first text node, before the <ul>)
            name_parts = []
            for child in name_td.children:
                if hasattr(child, "name") and child.name == "ul":
                    break
                text = child.get_text(strip=True) if hasattr(child, "get_text") else str(child).strip()
                if text:
                    name_parts.append(text)
            name = " ".join(name_parts).strip()

            # Extract response history from <li> elements
            lis = name_td.find_all("li")
            response_count = len(lis)
            last_response: str | None = None
            response_timestamp: str | None = None
            notes: str = ""

            if lis:
                last_li = lis[-1]
                b_tag = last_li.find("b")
                span_tag = last_li.find("span")
                div_tag = last_li.find("div")

                if b_tag:
                    response_timestamp = b_tag.get_text(strip=True)
                if span_tag:
                    last_response = span_tag.get_text(strip=True)
                if div_tag:
                    notes = div_tag.get_text(strip=True)

            status = _derive_util_status(row_status, last_response)

            records.append({
                "code": code,
                "name": name,
                "facility": facility,
                "row_status": row_status,
                "last_response": last_response,
                "response_timestamp": response_timestamp,
                "response_count": response_count,
                "notes": notes,
                "status": status,
            })

        break  # Only one Response Status table per page

    return records


def _extract_utility_codes(
    soup: BeautifulSoup,
    utility_statuses: list[dict[str, Any]] | None = None,
) -> list[str]:
    """
    Extract utility response codes for backward compatibility.

    Prefers the Utilities Notified table (Code | Name | Added Manually?),
    which has deduplicated entries. Falls back to codes from utility_statuses
    (Response Status table), then to a regex scan.
    """
    codes: list[str] = []
    seen: set[str] = set()

    def _add(code: str) -> None:
        c = code.strip().upper()
        if c and c not in seen:
            codes.append(c)
            seen.add(c)

    # Strategy 1: Utilities Notified table (first header cell is "Code")
    for table in soup.find_all("table"):
        header_row = table.find("tr")
        if not header_row:
            continue
        header_cells = header_row.find_all(["th", "td"])
        if not header_cells:
            continue
        if header_cells[0].get_text(strip=True).lower() != "code":
            continue
        all_rows = table.find_all("tr")
        for tr in all_rows[1:]:
            cells = tr.find_all(["td", "th"])
            if cells:
                code_text = cells[DetailPage.UTILITY_CODE_COL].get_text(strip=True)
                if code_text:
                    _add(code_text)
        if codes:
            return codes

    # Strategy 2: codes from the Response Status records already extracted
    if utility_statuses:
        for u in utility_statuses:
            _add(u["code"])
        if codes:
            return codes

    # Strategy 3: regex scan for short uppercase codes near utility context
    import re as _re
    raw = soup.get_text(separator="\n")
    for m in _re.finditer(
        r"\b([A-Z]{2,8})\b(?=\s*[-:|\s]?\s*(?:utility|notified|response|locate|marked))",
        raw,
        _re.IGNORECASE,
    ):
        _add(m.group(1))

    return codes
