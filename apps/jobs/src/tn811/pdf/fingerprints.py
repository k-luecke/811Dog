"""
pdf/fingerprints.py — Content fingerprinting for PDFs and HTML snapshots.

Used to detect when a ticket's underlying document has changed,
avoiding redundant parses and database writes.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file's raw bytes."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    """Return the SHA-256 hex digest of a UTF-8 string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pdf_already_parsed(
    pdf_path: Path,
    known_hash: str | None,
) -> bool:
    """
    Return True if the PDF at pdf_path has the same content as known_hash.

    If known_hash is None (first time seeing this ticket), returns False.
    """
    if known_hash is None:
        return False
    if not pdf_path.exists():
        return False
    return sha256_file(pdf_path) == known_hash
