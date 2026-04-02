"""
pdf/downloader.py — PDF download pipeline for TN811 ticket documents.

Design:
- PDFs are downloaded to deterministic paths based on ticket number.
- SHA-256 hash is computed after download.
- Duplicate downloads are avoided via hash comparison.
- Downloads use httpx (async HTTP) rather than Playwright, for simplicity
  and reliability. The Playwright session cookies are not needed for PDF
  downloads (they are public documents on TN811).
- Retries with exponential backoff on network errors.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from tn811.config import PortalConfig

logger = logging.getLogger(__name__)


def deterministic_pdf_path(raw_pdf_dir: str, ticket_number: str) -> Path:
    """
    Return the canonical local path for a ticket's PDF.

    Path format: {raw_pdf_dir}/{ticket_number}.pdf
    The ticket number is sanitized for filesystem safety.
    """
    safe_num = ticket_number.upper().replace("/", "_").replace("\\", "_").replace(":", "_")
    return Path(raw_pdf_dir) / f"{safe_num}.pdf"


def sha256_of_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class PDFDownloader:
    """
    Downloads ticket PDFs from TN811 portal URLs.

    Usage:
        downloader = PDFDownloader(portal_cfg, raw_pdf_dir)
        result = await downloader.download(ticket_number, pdf_url)
    """

    def __init__(self, portal_cfg: PortalConfig, raw_pdf_dir: str) -> None:
        self._cfg = portal_cfg
        self._raw_pdf_dir = raw_pdf_dir
        Path(raw_pdf_dir).mkdir(parents=True, exist_ok=True)

    async def download(
        self,
        ticket_number: str,
        pdf_url: str,
        force: bool = False,
    ) -> "DownloadResult":
        """
        Download a PDF for a ticket.

        Args:
            ticket_number: Used to construct the local path.
            pdf_url:       Remote URL to download from.
            force:         Re-download even if the file already exists.

        Returns:
            DownloadResult with path, hash, and was_cached flag.
        """
        dest = deterministic_pdf_path(self._raw_pdf_dir, ticket_number)

        if dest.exists() and not force:
            existing_hash = sha256_of_file(dest)
            logger.debug(
                "PDF already exists, skipping download",
                extra={"ticket": ticket_number, "hash": existing_hash},
            )
            return DownloadResult(
                ticket_number=ticket_number,
                path=dest,
                sha256=existing_hash,
                was_cached=True,
            )

        logger.info("Downloading PDF", extra={"ticket": ticket_number, "url": pdf_url})
        content = await self._fetch_with_retry(pdf_url)

        dest.write_bytes(content)
        file_hash = sha256_of_file(dest)

        logger.info(
            "PDF downloaded",
            extra={"ticket": ticket_number, "size": len(content), "hash": file_hash},
        )
        return DownloadResult(
            ticket_number=ticket_number,
            path=dest,
            sha256=file_hash,
            was_cached=False,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=16),
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        reraise=True,
    )
    async def _fetch_with_retry(self, url: str) -> bytes:
        timeout = self._cfg.timeout_ms / 1000
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            response = await client.get(
                url,
                headers={"User-Agent": "TN811Monitor/1.0 (internal monitoring tool)"},
            )
            response.raise_for_status()
            return response.content


from dataclasses import dataclass


@dataclass
class DownloadResult:
    ticket_number: str
    path: Path
    sha256: str
    was_cached: bool
