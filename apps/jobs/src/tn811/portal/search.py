"""
portal/search.py — TN811 portal search page interaction.

Implements the SearchAdapter that fills in the county + date form,
submits it, and yields structured TicketRow objects from the results.

SELECTOR BOUNDARY:
    All CSS selectors come from portal.selectors.SearchPage.
    If the portal markup changes, update selectors.py and the
    column-index constants — this file should not need to change.

ASSUMPTION LOG (see docs/parser_assumptions.md for full context):
    1. The search form is server-rendered HTML; no SPA navigation needed.
    2. Results appear in an HTML <table> after form submission.
    3. Pagination is handled via a "Next" link.
    4. Each result row contains the ticket number as a hyperlink to
       the detail page.
    5. Date format accepted by the form: MM/DD/YYYY.

    If any assumption is wrong, implement the fix in this file and
    update the corresponding fixture in tests/fixtures/html/.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import AsyncGenerator

from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, Page

from tn811.config import CountyConfig, PortalConfig
from tn811.portal.browser import first_matching_selector, navigate_with_retry, save_page_html
from tn811.portal.selectors import SearchPage

logger = logging.getLogger(__name__)


@dataclass
class TicketRow:
    """
    Lightweight record from a single row of the search results table.

    This is a pre-normalization snapshot — only the fields visible in
    the results table. The detail page + PDF provide the full record.
    """
    ticket_number: str
    county: str
    detail_url: str | None
    call_date_raw: str | None = None
    expiration_date_raw: str | None = None
    excavator_name_raw: str | None = None
    work_type_raw: str | None = None
    status_raw: str | None = None


class SearchAdapter:
    """
    Adapter for the TN811 public ticket search page.

    Responsible for:
    1. Navigating to the search page.
    2. Filling the county + date range form.
    3. Submitting and waiting for results.
    4. Parsing result rows.
    5. Handling pagination.
    6. Saving raw HTML snapshots.
    """

    def __init__(self, portal_cfg: PortalConfig, raw_html_dir: str) -> None:
        self._cfg = portal_cfg
        self._raw_html_dir = raw_html_dir
        self._search_url = portal_cfg.search_url

    async def search_county(
        self,
        ctx: BrowserContext,
        county: CountyConfig,
        date_from: date,
        date_to: date,
    ) -> AsyncGenerator[TicketRow, None]:
        """
        Async generator that yields TicketRow objects for all results
        in the given county and date range.

        Args:
            ctx:       Playwright BrowserContext.
            county:    County config.
            date_from: Start date for search window.
            date_to:   End date (usually today).

        Yields:
            TicketRow for each result row across all pages.
        """
        page: Page = await ctx.new_page()
        try:
            await self._load_search_page(page)
            await self._fill_form(page, county, date_from, date_to)
            await self._submit_form(page)

            page_num = 0
            while True:
                page_num += 1
                await self._save_snapshot(page, county.name, date_from, page_num)

                rows = await self._parse_result_rows(page, county)
                logger.info(
                    "Parsed result page",
                    extra={"county": county.name, "page": page_num, "rows": len(rows)},
                )
                for row in rows:
                    yield row

                # Check for next page
                next_sel = await first_matching_selector(page, SearchPage.NEXT_PAGE_CANDIDATES)
                if next_sel is None:
                    break

                next_link = await page.query_selector(next_sel)
                if next_link is None:
                    break

                await next_link.click()
                await page.wait_for_load_state("networkidle")
        finally:
            await page.close()

    async def _load_search_page(self, page: Page) -> None:
        logger.info("Loading search page", extra={"url": self._search_url})
        await navigate_with_retry(page, self._search_url, self._cfg)

    async def _fill_form(
        self,
        page: Page,
        county: CountyConfig,
        date_from: date,
        date_to: date,
    ) -> None:
        """Fill the county dropdown and date range inputs."""
        # County dropdown
        county_sel = await first_matching_selector(page, SearchPage.COUNTY_SELECT_CANDIDATES)
        if county_sel is None:
            raise RuntimeError(
                f"Could not find county dropdown on {self._search_url}. "
                "Update portal/selectors.py with the correct selector. "
                "See docs/parser_assumptions.md for procedure."
            )
        await page.select_option(county_sel, label=county.portal_search_value)
        logger.debug("Selected county", extra={"county": county.portal_search_value})

        # Date inputs (MM/DD/YYYY format)
        date_from_str = date_from.strftime("%m/%d/%Y")
        date_to_str = date_to.strftime("%m/%d/%Y")

        from_sel = await first_matching_selector(page, SearchPage.DATE_FROM_CANDIDATES)
        if from_sel:
            await page.fill(from_sel, date_from_str)

        to_sel = await first_matching_selector(page, SearchPage.DATE_TO_CANDIDATES)
        if to_sel:
            await page.fill(to_sel, date_to_str)

        logger.debug("Filled date range", extra={"from": date_from_str, "to": date_to_str})

    async def _submit_form(self, page: Page) -> None:
        submit_sel = await first_matching_selector(page, SearchPage.SUBMIT_CANDIDATES)
        if submit_sel is None:
            raise RuntimeError(
                f"Could not find search submit button on {self._search_url}. "
                "Update SearchPage.SUBMIT_CANDIDATES in portal/selectors.py."
            )
        await page.click(submit_sel)
        await page.wait_for_load_state("networkidle")

    async def _save_snapshot(
        self, page: Page, county_name: str, date_from: date, page_num: int
    ) -> None:
        safe_county = county_name.lower().replace(" ", "_")
        path = (
            f"{self._raw_html_dir}/search_{safe_county}"
            f"_{date_from.isoformat()}_page{page_num}.html"
        )
        await save_page_html(page, path)

    async def _parse_result_rows(self, page: Page, county: CountyConfig) -> list[TicketRow]:
        """
        Parse result rows from the current page using BeautifulSoup.

        Falls back to an empty list (not an exception) if the results table
        is not found — this means no tickets for this county/date.
        """
        html = await page.content()
        return parse_search_results_html(html, county.name, self._cfg.base_url)


def parse_search_results_html(html: str, county_name: str, base_url: str) -> list[TicketRow]:
    """
    Parse TN811 search results HTML into TicketRow objects.

    This function is pure (no I/O) and tested against HTML fixtures.
    It is the canonical parsing logic — the Playwright adapter calls it,
    and tests call it directly with fixture HTML.

    Args:
        html:        Raw HTML of the search results page.
        county_name: County name to attach to each row.
        base_url:    Portal base URL for resolving relative links.

    Returns:
        List of TicketRow objects (may be empty if no results).
    """
    soup = BeautifulSoup(html, "html.parser")

    # Check for explicit no-results indicators
    for sel in SearchPage.NO_RESULTS_CANDIDATES:
        # BeautifulSoup doesn't support Playwright :has-text() pseudo-selectors
        # so we search by tag + text content
        tag, _, text = sel.partition(":has-text('")
        if text:
            text = text.rstrip("')")
            matches = soup.find_all(tag.strip(".#") or True, string=lambda s: s and text.lower() in s.lower())
            if matches:
                logger.info("No results indicator found", extra={"county": county_name})
                return []

    # Find the results table
    table = None
    for candidate_sel in SearchPage.RESULTS_TABLE_CANDIDATES:
        # Convert CSS-ish selector to BS4-friendly lookup
        table = _find_by_css_candidate(soup, candidate_sel)
        if table is not None:
            break

    if table is None:
        logger.warning(
            "No results table found in HTML",
            extra={"county": county_name, "html_length": len(html)},
        )
        return []

    rows: list[TicketRow] = []
    tbody = table.find("tbody") or table

    for tr in tbody.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue

        # Skip header rows
        if tr.find("th"):
            continue

        try:
            row = _parse_result_row(cells, county_name, base_url)
            if row is not None:
                rows.append(row)
        except Exception as exc:
            logger.warning(
                "Failed to parse result row",
                extra={"county": county_name, "error": str(exc)},
            )

    return rows


def _parse_result_row(cells: list, county_name: str, base_url: str) -> TicketRow | None:
    """Parse a single <tr> cell list into a TicketRow."""
    if len(cells) < 2:
        return None

    # Extract text content from each cell
    cell_texts = [c.get_text(strip=True) for c in cells]

    # Ticket number cell — look for a link
    ticket_cell = cells[SearchPage.COL_TICKET_NUMBER]
    link = ticket_cell.find("a")
    ticket_number = ""
    detail_url = None

    if link:
        ticket_number = link.get_text(strip=True)
        href = link.get("href", "")
        if href:
            detail_url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
    else:
        ticket_number = cell_texts[SearchPage.COL_TICKET_NUMBER]

    if not ticket_number:
        return None

    def safe_cell(idx: int) -> str | None:
        return cell_texts[idx] if idx < len(cell_texts) else None

    return TicketRow(
        ticket_number=ticket_number.strip().upper(),
        county=county_name,
        detail_url=detail_url,
        call_date_raw=safe_cell(SearchPage.COL_CALL_DATE),
        expiration_date_raw=safe_cell(SearchPage.COL_EXPIRATION_DATE),
        excavator_name_raw=safe_cell(SearchPage.COL_EXCAVATOR),
        work_type_raw=safe_cell(SearchPage.COL_WORK_TYPE),
        status_raw=safe_cell(SearchPage.COL_STATUS),
    )


def _find_by_css_candidate(soup: BeautifulSoup, selector: str):
    """
    Best-effort BeautifulSoup lookup for simple CSS selectors.
    Handles: tag, tag.class, tag#id, #id, .class
    """
    import re
    selector = selector.strip()
    # tag#id
    m = re.match(r'^(\w+)#([\w-]+)$', selector)
    if m:
        return soup.find(m.group(1), id=m.group(2))
    # tag.class
    m = re.match(r'^(\w+)\.([\w-]+)$', selector)
    if m:
        return soup.find(m.group(1), class_=m.group(2))
    # #id
    m = re.match(r'^#([\w-]+)$', selector)
    if m:
        return soup.find(id=m.group(1))
    # .class
    m = re.match(r'^\.([\w-]+)$', selector)
    if m:
        return soup.find(class_=m.group(1))
    # bare tag
    m = re.match(r'^(\w+)$', selector)
    if m:
        return soup.find(m.group(1))
    # container > tag (e.g., "#ticket-results table")
    parts = selector.rsplit(None, 1)
    if len(parts) == 2:
        container = _find_by_css_candidate(soup, parts[0])
        if container:
            return _find_by_css_candidate(container, parts[1])
    return None
