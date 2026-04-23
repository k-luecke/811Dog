"""
normalize/tickets.py — Ticket normalization pipeline.

Takes raw inputs (DetailRecord + TicketRow) and produces a NormalizedTicket
with validated, cleaned fields.

The normalizer is the single place where:
- date strings are parsed into date objects
- text is stripped / truncated
- ticket number format is validated
- status is derived
- detail page fields override search row fields
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone

from tn811.models import NormalizedTicket
from tn811.pdf.extractor import parse_date
from tn811.portal.detail import DetailRecord
from tn811.portal.search import TicketRow

logger = logging.getLogger(__name__)

# Live portal: pure integers (e.g. 2609013987)
_TICKET_NUM_PATTERN = re.compile(r"^\d{10,}$")
# Legacy format from test fixtures: TN20240101-123456
_TICKET_NUM_PATTERN_LEGACY = re.compile(r"^TN\d{8}-\d{6}$", re.IGNORECASE)


def normalize_ticket(
    row: TicketRow,
    detail: DetailRecord | None,
    now: datetime | None = None,
) -> NormalizedTicket:
    """
    Produce a NormalizedTicket from search row and detail page.

    Merge priority (highest wins):
      Detail page fields > Search result row fields

    Args:
        row:    Raw search result row (always present).
        detail: Parsed detail page record (may be None if skipped).
        now:    Timestamp for parsed_at (defaults to UTC now).

    Returns:
        NormalizedTicket with all available fields populated.
    """
    now = now or datetime.now(timezone.utc)

    # ── Ticket number ────────────────────────────────────────────────────────
    ticket_number = _best_str(
        detail.fields.get("ticket_number") if detail else None,
        row.ticket_number,
    )
    ticket_number = (ticket_number or "").strip().upper()

    # ── County ───────────────────────────────────────────────────────────────
    county = _best_str(
        detail.county if detail else None,
        row.county,
    ) or ""

    # ── Dates ────────────────────────────────────────────────────────────────
    call_date = _parse_date_layered(
        detail.fields.get("call_date") if detail else None,
        row.call_date_raw,
    )
    legal_start_date = _parse_date_layered(
        detail.fields.get("legal_start_date") if detail else None,
    )
    expiration_date = _parse_date_layered(
        detail.fields.get("expiration_date") if detail else None,
        row.expiration_date_raw,
    )

    # ── Parties ───────────────────────────────────────────────────────────────
    excavator_name = _clean_str(_best_str(
        detail.fields.get("excavator_name") if detail else None,
        row.excavator_name_raw,
    ))
    caller_name = _clean_str(_best_str(
        detail.fields.get("caller_name") if detail else None,
    ))
    caller_phone = _clean_str(_best_str(
        detail.fields.get("caller_phone") if detail else None,
    ))

    # ── Work details ──────────────────────────────────────────────────────────
    work_type = _clean_str(_best_str(
        detail.fields.get("work_type") if detail else None,
        row.work_type_raw,
    ))
    location_text = _clean_str(_best_str(
        detail.fields.get("location_text") if detail else None,
    ))
    remarks = _clean_str(_best_str(
        detail.fields.get("remarks") if detail else None,
    ))
    done_for = _clean_str(_best_str(
        detail.fields.get("done_for") if detail else None,
    ))

    # ── Utilities ─────────────────────────────────────────────────────────────
    utility_codes = (detail.utility_codes if detail else []) or []

    # ── Cancellation ──────────────────────────────────────────────────────────
    is_cancelled = bool(
        (detail and detail.is_cancelled)
        or _looks_cancelled(row.status_raw)
    )

    # ── Source metadata ───────────────────────────────────────────────────────
    source_html_path = detail.html_snapshot_path if detail else None
    parse_method = "html" if detail else "row_only"
    raw_text = (detail.raw_text if detail else None) or ""

    ticket = NormalizedTicket(
        ticket_number=ticket_number,
        county=county,
        state="TN",
        call_date=call_date,
        legal_start_date=legal_start_date,
        expiration_date=expiration_date,
        excavator_name=excavator_name,
        caller_name=caller_name,
        caller_phone=caller_phone,
        work_type=work_type,
        location_text=location_text,
        remarks=remarks,
        done_for=done_for,
        utility_references=[],
        utility_responses=[],
        utility_codes=utility_codes,
        is_cancelled=is_cancelled,
        source_html_path=source_html_path,
        raw_text=raw_text,
        parsed_at=now,
        parse_method=parse_method,
    )

    logger.debug(
        "Normalized ticket",
        extra={
            "ticket": ticket_number,
            "county": county,
            "status": ticket.status.value,
            "expiration": expiration_date.isoformat() if expiration_date else None,
        },
    )
    return ticket


def validate_ticket_number(ticket_number: str) -> bool:
    """Return True if the ticket number matches either the live portal format or legacy format."""
    return bool(
        _TICKET_NUM_PATTERN.match(ticket_number)
        or _TICKET_NUM_PATTERN_LEGACY.match(ticket_number)
    )


# ── Private helpers ────────────────────────────────────────────────────────────

def _best_str(*candidates: str | None) -> str | None:
    """Return the first non-None, non-empty string from candidates."""
    for c in candidates:
        if c and c.strip():
            return c.strip()
    return None


def _clean_str(value: str | None, max_len: int = 500) -> str | None:
    """Normalize whitespace and truncate."""
    if not value:
        return None
    cleaned = " ".join(value.split())
    return cleaned[:max_len] if cleaned else None


def _parse_date_layered(*raw_strings: str | None) -> date | None:
    """Return the first successfully parsed date from the given raw strings."""
    for raw in raw_strings:
        if raw:
            parsed = parse_date(raw)
            if parsed:
                return parsed
    return None


def _looks_cancelled(status_raw: str | None) -> bool:
    if not status_raw:
        return False
    return "cancel" in status_raw.lower() or "void" in status_raw.lower()
