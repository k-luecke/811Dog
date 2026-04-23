"""
portal/search.py — TN811 portal search page interaction.

Implements the SearchAdapter that fills in the county + date form,
clicks Export, and yields structured TicketRow objects from the CSV download.

SELECTOR BOUNDARY:
    All CSS/XPath selectors come from portal.selectors.SearchPage.
    If the portal markup changes, update selectors.py — this file should not change.

ASSUMPTION LOG (see comments in selectors.py for full context):
    1. The portal is an ExtJS SPA; county selection requires trigger-click + boundlist.
    2. Date fields are plain <input> elements; triple-click before fill to clear.
    3. The Search button (NOT the nav Find Tickets button) triggers the XHR.
    4. Export button → immediate CSV download, no modal/dialog.
    5. CSV column order may vary; always use the header row to map column names.
    6. Date format accepted by the form: MM/DD/YYYY.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import AsyncGenerator

from playwright.async_api import BrowserContext, Page

from tn811.config import CountyConfig, PortalConfig
from tn811.portal.browser import navigate_with_retry, save_page_html
from tn811.portal.selectors import DetailPage, SearchPage

logger = logging.getLogger(__name__)


@dataclass
class TicketRow:
    """
    Lightweight record from a single row of the CSV export.

    This is a pre-normalization snapshot. Detail page fetch is gated by
    _worth_detail_fetch() in cli.py — only relevant candidates are fetched.
    """
    ticket_number: str           # CSV 'Number' — human-readable ticket ID
    county: str
    detail_url: str | None       # Constructed from DetailPage.URL_TEMPLATE + ticket_id
    ticket_id: str = ""          # CSV 'ticketId' — used to construct detail URL
    call_date_raw: str | None = None
    expiration_date_raw: str | None = None
    excavator_name_raw: str | None = None
    work_type_raw: str | None = None
    status_raw: str | None = None        # CSV 'TicketType' (Normal/Cancelled/etc.)
    work_done_for_raw: str | None = None  # CSV 'WorkDoneFor' — primary relevant pre-filter
    remarks_raw: str | None = None        # CSV 'Remarks'


class SearchAdapter:
    """
    Adapter for the TN811 public ticket search page.

    Responsible for:
    1. Navigating to the ExtJS search page.
    2. Filling the county combobox and date range inputs.
    3. Submitting the search and waiting for grid results.
    4. Clicking Export and downloading the full CSV.
    5. Parsing CSV rows into TicketRow objects.
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
        Async generator that yields TicketRow objects for the given county/date range.

        Navigates the portal, submits the search, clicks Export, parses the CSV.
        """
        page: Page = await ctx.new_page()
        try:
            await self._load_search_page(page)
            await self._fill_form(page, county, date_from, date_to)
            await self._submit_form(page)
            await self._save_snapshot(page, county.name, date_from)

            rows = await self._download_and_parse_export(page, county)
            for row in rows:
                yield row
        finally:
            await page.close()

    async def _load_search_page(self, page: Page) -> None:
        logger.info("Loading search page", extra={"url": self._search_url})
        await navigate_with_retry(page, self._search_url, self._cfg)
        await asyncio.sleep(2)  # ExtJS initialization

    async def _fill_form(
        self,
        page: Page,
        county: CountyConfig,
        date_from: date,
        date_to: date,
    ) -> None:
        """Fill the ExtJS county combobox and date range inputs."""
        # Open the ExtJS combobox via its arrow trigger
        trigger = page.locator(SearchPage.COUNTY_TRIGGER_XPATH)
        await trigger.click()
        await page.wait_for_selector(
            SearchPage.COUNTY_BOUNDLIST_ITEM, state="visible", timeout=10000
        )

        # Click the matching county item in the boundlist overlay
        target = county.portal_search_value.upper()
        matched = False
        for item in await page.locator(SearchPage.COUNTY_BOUNDLIST_ITEM).all():
            if (await item.inner_text()).strip().upper() == target:
                await item.click()
                matched = True
                break

        if not matched:
            raise RuntimeError(
                f"County '{county.portal_search_value}' not found in portal dropdown. "
                "Check config portal_search_value or update selectors.py."
            )

        await asyncio.sleep(0.3)
        logger.debug("Selected county", extra={"county": county.portal_search_value})

        # Fill date inputs (triple-click to clear pre-filled value, then fill)
        date_from_str = date_from.strftime("%m/%d/%Y")
        date_to_str = date_to.strftime("%m/%d/%Y")

        from_loc = page.locator(SearchPage.DATE_FROM).first
        await from_loc.click(click_count=3)
        await from_loc.fill(date_from_str)

        to_loc = page.locator(SearchPage.DATE_TO).first
        await to_loc.click(click_count=3)
        await to_loc.fill(date_to_str)

        logger.debug("Filled date range", extra={"from": date_from_str, "to": date_to_str})

    async def _submit_form(self, page: Page) -> None:
        """Click the Search button and wait for the ExtJS grid to render."""
        await page.locator(SearchPage.SEARCH_BUTTON).first.click()
        await page.wait_for_load_state("networkidle", timeout=30000)
        await asyncio.sleep(5)  # ExtJS grid render latency after networkidle

    async def _save_snapshot(
        self, page: Page, county_name: str, date_from: date
    ) -> None:
        safe_county = county_name.lower().replace(" ", "_")
        path = (
            f"{self._raw_html_dir}/search_{safe_county}"
            f"_{date_from.isoformat()}_grid.html"
        )
        await save_page_html(page, path)

    async def _download_and_parse_export(
        self, page: Page, county: CountyConfig
    ) -> list[TicketRow]:
        """Click Export, save the CSV to a temp dir, parse and return rows."""
        async with page.expect_download(timeout=30000) as dl_info:
            await page.locator(SearchPage.EXPORT_BUTTON).first.click()
        download = await dl_info.value

        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / (download.suggested_filename or "export.csv")
            await download.save_as(str(save_path))
            csv_text = save_path.read_text(encoding="utf-8", errors="replace")

        rows = parse_export_csv(csv_text, county.name)
        logger.info(
            "Export downloaded and parsed",
            extra={
                "county": county.name,
                "rows": len(rows),
                "bytes": len(csv_text),
            },
        )
        return rows


def parse_export_csv(csv_text: str, county_name: str) -> list[TicketRow]:
    """
    Parse TN811 export CSV text into TicketRow objects.

    This function is pure (no I/O) and tested against CSV fixtures.
    Column positions are determined from the header row, not hard-coded indices.

    Args:
        csv_text:    Raw CSV text from the portal Export button.
        county_name: County name to attach to each row.

    Returns:
        List of TicketRow objects (may be empty if no results or header-only).
    """
    rows: list[TicketRow] = []
    reader = csv.reader(io.StringIO(csv_text))
    idx: dict[str, int] = {}
    header_seen = False

    for line in reader:
        if not header_seen:
            idx = {h.strip(): i for i, h in enumerate(line)}
            header_seen = True
            continue
        if len(line) < 4:
            continue
        try:
            row = _parse_csv_row(line, idx, county_name)
            if row is not None:
                rows.append(row)
        except Exception as exc:
            logger.warning("Failed to parse CSV row", extra={"error": str(exc)})

    return rows


def _parse_csv_row(
    cells: list[str], idx: dict[str, int], county_name: str
) -> TicketRow | None:
    """Parse one CSV data row into a TicketRow using header-derived column indices."""

    def col(name: str) -> str | None:
        i = idx.get(name)
        if i is None or i >= len(cells):
            return None
        v = cells[i].strip()
        return v if v else None

    ticket_id = col("ticketId") or ""
    ticket_number = col("Number") or ""
    if not ticket_id or not ticket_number:
        return None

    detail_url = DetailPage.URL_TEMPLATE.format(ticket_id=ticket_id)

    return TicketRow(
        ticket_number=ticket_number.strip().upper(),
        ticket_id=ticket_id,
        county=county_name,
        detail_url=detail_url,
        call_date_raw=col("Creation"),
        expiration_date_raw=col("GoodUntil"),
        excavator_name_raw=col("ExcavatorName"),
        work_type_raw=col("workType"),
        status_raw=col("TicketType"),
        work_done_for_raw=col("WorkDoneFor"),
        remarks_raw=col("Remarks"),
    )
