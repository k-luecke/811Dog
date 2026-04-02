"""
portal/browser.py — Playwright browser lifecycle management.

Provides a context manager that owns a Playwright browser instance.
All portal navigation happens through the Page objects it yields.

Design notes:
- Headless/headed mode is driven by config.
- A single browser context is used per scrape run (shares cookies/session).
- Individual pages are created per task (search, detail) to avoid state leakage.
- Retries with exponential backoff are applied at the navigation level.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tn811.config import PortalConfig

logger = logging.getLogger(__name__)


@asynccontextmanager
async def browser_context(
    portal_cfg: PortalConfig,
) -> AsyncGenerator[BrowserContext, None]:
    """
    Async context manager that yields a Playwright BrowserContext.

    Usage:
        async with browser_context(config.portal) as ctx:
            page = await ctx.new_page()
            await page.goto(url)
    """
    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(
            headless=portal_cfg.headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx: BrowserContext = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (compatible; TN811Monitor/1.0; "
                "internal monitoring tool)"
            ),
            viewport={"width": 1280, "height": 900},
        )
        ctx.set_default_timeout(portal_cfg.timeout_ms)
        try:
            yield ctx
        finally:
            await ctx.close()
            await browser.close()


async def navigate_with_retry(
    page: Page,
    url: str,
    portal_cfg: PortalConfig,
    wait_until: str = "networkidle",
) -> None:
    """
    Navigate to a URL with retry/backoff.

    Args:
        page:       Playwright Page.
        url:        Target URL.
        portal_cfg: Portal config for retry settings.
        wait_until: Playwright load-state to wait for.
    """
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(portal_cfg.max_retries),
        wait=wait_exponential(
            multiplier=portal_cfg.retry_backoff_base_s,
            min=portal_cfg.retry_backoff_base_s,
            max=portal_cfg.retry_backoff_base_s * 8,
        ),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    ):
        with attempt:
            logger.debug("Navigating", extra={"url": url, "attempt": attempt.retry_state.attempt_number})
            await page.goto(url, wait_until=wait_until, timeout=portal_cfg.timeout_ms)


async def first_matching_selector(page: Page, candidates: list[str]) -> str | None:
    """
    Return the first selector from candidates that exists on the current page.

    This is used to handle portal markup variability without brittle
    hardcoded single selectors.

    Args:
        page:       Playwright Page (must already be navigated).
        candidates: List of CSS selector strings to try in order.

    Returns:
        The first matching selector string, or None if none match.
    """
    for selector in candidates:
        try:
            element = await page.query_selector(selector)
            if element is not None:
                return selector
        except Exception:
            continue
    return None


async def save_page_html(page: Page, path: str) -> None:
    """Save the current page HTML to disk for debugging and fixtures."""
    from pathlib import Path
    html = await page.content()
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(html, encoding="utf-8")
    logger.debug("Saved page HTML", extra={"path": path, "size": len(html)})
