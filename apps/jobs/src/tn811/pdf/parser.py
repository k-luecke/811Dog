"""
pdf/parser.py — PDF parsing pipeline for TN811 ticket documents.

Parse strategy:
1. Try pdfplumber (primary) — best for text-layer PDFs.
2. Fall back to PyMuPDF if pdfplumber returns empty/garbled text.
3. OCR fallback (pytesseract) is opt-in and disabled by default.

Each parser returns a RawPDFText object containing:
- Full concatenated text
- Per-page text list
- Parser name used
- Any warnings

The actual field extraction from raw text is in pdf/extractor.py.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from tn811.config import PdfConfig

logger = logging.getLogger(__name__)


@dataclass
class RawPDFText:
    """Raw text output from a PDF parser."""
    full_text: str
    pages: list[str] = field(default_factory=list)
    parser_used: str = ""
    warnings: list[str] = field(default_factory=list)
    page_count: int = 0


class PDFParser:
    """
    Orchestrates PDF parsing with fallback chain.

    Usage:
        parser = PDFParser(pdf_cfg)
        raw = parser.parse(path)
    """

    def __init__(self, pdf_cfg: PdfConfig) -> None:
        self._cfg = pdf_cfg

    def parse(self, path: Path) -> RawPDFText:
        """
        Parse a PDF file, applying the configured fallback chain.

        Args:
            path: Path to the PDF file.

        Returns:
            RawPDFText with extracted text.

        Raises:
            RuntimeError: If all parsers fail.
        """
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {path}")

        result = None

        # Primary: pdfplumber
        if self._cfg.primary_parser == "pdfplumber":
            try:
                result = _parse_pdfplumber(path)
                if _is_usable(result):
                    logger.debug("pdfplumber succeeded", extra={"path": str(path)})
                    return result
                else:
                    logger.warning(
                        "pdfplumber returned empty/short text, trying fallback",
                        extra={"path": str(path), "text_len": len(result.full_text)},
                    )
            except Exception as exc:
                logger.warning(
                    "pdfplumber failed", extra={"path": str(path), "error": str(exc)}
                )

        # Fallback: PyMuPDF
        if self._cfg.fallback_to_pymupdf:
            try:
                result = _parse_pymupdf(path)
                if _is_usable(result):
                    logger.debug("PyMuPDF succeeded", extra={"path": str(path)})
                    return result
                else:
                    logger.warning(
                        "PyMuPDF returned empty text",
                        extra={"path": str(path)},
                    )
            except Exception as exc:
                logger.warning(
                    "PyMuPDF failed", extra={"path": str(path), "error": str(exc)}
                )

        # OCR fallback (disabled by default)
        if self._cfg.ocr_enabled:
            try:
                result = _parse_ocr(path, self._cfg.tesseract_path)
                if _is_usable(result):
                    logger.info("OCR succeeded", extra={"path": str(path)})
                    return result
            except Exception as exc:
                logger.error(
                    "OCR failed", extra={"path": str(path), "error": str(exc)}
                )

        # All parsers failed — return best partial result or raise
        if result is not None:
            result.warnings.append("All parsers returned empty/short text")
            return result

        raise RuntimeError(f"All PDF parsers failed for {path}")


def _is_usable(result: RawPDFText, min_chars: int = 50) -> bool:
    """Return True if the parsed text is long enough to be useful."""
    return len(result.full_text.strip()) >= min_chars


def _parse_pdfplumber(path: Path) -> RawPDFText:
    """Parse a PDF using pdfplumber."""
    import pdfplumber

    pages: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        page_count = len(pdf.pages)
        for page in pdf.pages:
            text = page.extract_text() or ""
            pages.append(text)

    full_text = "\n".join(pages)
    return RawPDFText(
        full_text=full_text,
        pages=pages,
        parser_used="pdfplumber",
        page_count=page_count,
    )


def _parse_pymupdf(path: Path) -> RawPDFText:
    """Parse a PDF using PyMuPDF (fitz)."""
    import fitz  # PyMuPDF

    pages: list[str] = []
    with fitz.open(str(path)) as doc:
        page_count = doc.page_count
        for page in doc:
            text = page.get_text("text") or ""
            pages.append(text)

    full_text = "\n".join(pages)
    return RawPDFText(
        full_text=full_text,
        pages=pages,
        parser_used="pymupdf",
        page_count=page_count,
    )


def _parse_ocr(path: Path, tesseract_path: str) -> RawPDFText:
    """
    Parse a PDF by rasterizing pages and running OCR.

    Requires: pytesseract + Pillow + tesseract binary.
    OCR is disabled by default (pdf.ocr_enabled: false in config).
    """
    try:
        import pytesseract
        from PIL import Image
        import fitz  # PyMuPDF for rasterization
    except ImportError as e:
        raise RuntimeError(
            f"OCR dependencies not installed: {e}. "
            "Install with: pip install 'tn811[ocr]'"
        ) from e

    pytesseract.pytesseract.tesseract_cmd = tesseract_path

    pages: list[str] = []
    with fitz.open(str(path)) as doc:
        page_count = doc.page_count
        for page in doc:
            # Render at 300 DPI for good OCR accuracy
            mat = fitz.Matrix(300 / 72, 300 / 72)
            pix = page.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            text = pytesseract.image_to_string(img, lang="eng")
            pages.append(text)

    full_text = "\n".join(pages)
    return RawPDFText(
        full_text=full_text,
        pages=pages,
        parser_used="ocr",
        page_count=page_count,
    )
