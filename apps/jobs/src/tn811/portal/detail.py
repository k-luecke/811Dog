"""
portal/detail.py — TN811 ticket detail page scraper.

Fetches the detail page for a single ticket, saves raw HTML, extracts
structured field values, discovers the PDF download URL, and marks
cancellation status.

SELECTOR BOUNDARY: All selectors come from portal.selectors.DetailPage.

ASSUMPTION LOG:
    The detail page is a server-rendered table of key-value pairs
    (e.g., <th>Ticket Number</th><td>TN20240101-123456</td>).
    Utility responses appear in a nested table or list.
    There is exactly one PDF link per ticket.

    If the detail page uses a different layout, update DetailPage selectors
    and _parse_detail_html() in this file. Add a new HTML fixture to
    tests/fixtures/html/ and update tests/portal/test_detail.py.
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

    # Utility response codes extracted from the utility table (e.g. ["GFI", "NES"])
    utility_codes: list[str] = field(default_factory=list)

    # PDF download URL discovered on the page (None if not found)
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
            ctx:           Playwright BrowserContext.
            ticket_number: Ticket identifier (for snapshot naming).
            county:        County name.
            detail_url:    Full URL to the detail page.

        Returns:
            DetailRecord with extracted fields and PDF URL.
        """
        page: Page = await ctx.new_page()
        try:
            logger.info("Fetching detail page", extra={"ticket": ticket_number, "url": detail_url})
            await navigate_with_retry(page, detail_url, self._cfg)

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
    1. Look for a <table> with key-value row pairs (th+td or two td columns).
    2. Map label text to canonical field names using DetailPage.LABEL_TO_FIELD.
    3. Discover the PDF link.
    4. Check for cancellation indicators.
    5. Capture full page text as raw_text fallback.
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

    # Strategy 2: Two-column <td><td> tables (label | value)
    if not fields:
        for row in soup.find_all("tr"):
            tds = row.find_all("td")
            if len(tds) >= 2:
                label = tds[0].get_text(strip=True).lower().rstrip(":")
                value = tds[1].get_text(strip=True)
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

    # ── PDF link ────────────────────────────────────────────────────────────
    for sel in DetailPage.PDF_LINK_CANDIDATES:
        link = _find_pdf_link(soup, sel, base_url)
        if link:
            record.pdf_url = link
            break

    # ── Cancellation ────────────────────────────────────────────────────────
    cancelled_text_signals = ["cancelled", "canceled", "void"]
    page_text_lower = record.raw_text.lower()
    for sig in cancelled_text_signals:
        if sig in page_text_lower:
            # Confirm it's not just a mention in a field label
            # Look for it in prominent positions
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
            "pdf_url": record.pdf_url,
            "cancelled": record.is_cancelled,
        },
    )
    return record


def _find_pdf_link(soup: BeautifulSoup, selector: str, base_url: str) -> str | None:
    """
    Find a PDF download link using a simplified selector.

    Handles:
    - a[href$='.pdf'] — links ending in .pdf
    - a:has-text('PDF') — links with PDF text (BS4 approximation)
    - a[href*='pdf'] — links containing 'pdf' in href
    """
    import re

    # href$='.pdf'
    m = re.match(r"a\[href\$='([^']+)'\]", selector)
    if m:
        suffix = m.group(1)
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.lower().endswith(suffix.lower()):
                return _resolve_url(href, base_url)

    # href*='text'
    m = re.match(r"a\[href\*='([^']+)'\]", selector)
    if m:
        fragment = m.group(1).lower()
        for a in soup.find_all("a", href=True):
            if fragment in a["href"].lower():
                return _resolve_url(a["href"], base_url)

    # :has-text('TEXT')
    m = re.match(r"a:has-text\('([^']+)'\)", selector)
    if m:
        text = m.group(1).lower()
        for a in soup.find_all("a"):
            if text in a.get_text(strip=True).lower():
                href = a.get("href", "")
                if href:
                    return _resolve_url(href, base_url)

    # bare 'a' selector
    if selector.strip() == "a":
        a = soup.find("a", href=True)
        if a:
            return _resolve_url(a["href"], base_url)

    return None


def _resolve_url(href: str, base_url: str) -> str:
    """Resolve a relative URL against the portal base URL."""
    if href.startswith("http"):
        return href
    return base_url.rstrip("/") + "/" + href.lstrip("/")


def _extract_utility_codes(soup: BeautifulSoup) -> list[str]:
    """
    Extract utility response codes from the utility table on the detail page.

    Strategy 1: Find a dedicated utility table and grab the first column of
                each body row (e.g. "GFI", "NES", "MLGW").
    Strategy 2: Fall back to scanning <li> items in a utilities section.
    Strategy 3: Regex scan the raw page text for short uppercase codes that
                appear next to "utility" / "response" context.

    Returns a deduplicated list of uppercase code strings.
    """
    from tn811.portal.selectors import DetailPage

    codes: list[str] = []
    seen: set[str] = set()

    def _add(code: str) -> None:
        c = code.strip().upper()
        if c and c not in seen:
            codes.append(c)
            seen.add(c)

    # Strategy 1: dedicated utility table
    util_table = None
    for sel in DetailPage.UTILITY_TABLE_CANDIDATES:
        util_table = _find_soup_element(soup, sel)
        if util_table is not None:
            break

    if util_table is not None:
        tbody = util_table.find("tbody") or util_table
        for tr in tbody.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if cells:
                code_text = cells[DetailPage.UTILITY_CODE_COL].get_text(strip=True)
                if code_text:
                    _add(code_text)
        if codes:
            return codes

    # Strategy 2: <ul>/<ol> list inside a utilities section header
    for heading in soup.find_all(["h2", "h3", "h4", "strong", "b"]):
        if "utilit" in heading.get_text(strip=True).lower():
            container = heading.find_next_sibling(["ul", "ol", "table"])
            if container is None:
                container = heading.find_next("ul") or heading.find_next("ol")
            if container:
                for item in container.find_all("li"):
                    text = item.get_text(strip=True)
                    # Grab a leading all-caps token as the code
                    import re as _re
                    m = _re.match(r"^([A-Z0-9]{2,10})\b", text)
                    if m:
                        _add(m.group(1))
                    elif text:
                        _add(text[:20])
    if codes:
        return codes

    # Strategy 3: regex scan for short uppercase codes near utility keywords
    import re as _re
    raw = soup.get_text(separator="\n")
    for m in _re.finditer(
        r"\b([A-Z]{2,8})\b(?=\s*[-:|\s]?\s*(?:utility|notified|response|locate|marked))",
        raw,
        _re.IGNORECASE,
    ):
        _add(m.group(1))

    return codes


def _find_soup_element(soup: BeautifulSoup, selector: str):
    """Best-effort BS4 lookup for simple CSS selectors (mirrors search.py helper)."""
    import re
    selector = selector.strip()
    m = re.match(r'^(\w+)#([\w-]+)$', selector)
    if m:
        return soup.find(m.group(1), id=m.group(2))
    m = re.match(r'^(\w+)\.([\w-]+)$', selector)
    if m:
        return soup.find(m.group(1), class_=m.group(2))
    m = re.match(r'^#([\w-]+)$', selector)
    if m:
        return soup.find(id=m.group(1))
    m = re.match(r'^\.([\w-]+)$', selector)
    if m:
        return soup.find(class_=m.group(1))
    parts = selector.rsplit(None, 1)
    if len(parts) == 2:
        container = _find_soup_element(soup, parts[0])
        if container:
            return _find_soup_element(container, parts[1])
    m = re.match(r'^(\w+)$', selector)
    if m:
        return soup.find(m.group(1))
    return None
