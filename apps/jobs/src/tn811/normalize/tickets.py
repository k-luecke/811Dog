"""
normalize/tickets.py — Ticket normalization pipeline.

Takes raw inputs (DetailRecord + ExtractedFields + TicketRow) and
produces a NormalizedTicket with validated, cleaned fields.

The normalizer is the single place where:
- date strings are parsed into date objects
- text is stripped / truncated
- ticket number format is validated
- status is derived
- fields from multiple sources are merged (detail page overrides row,
  PDF overrides both where PDF has better data)
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone

from tn811.models import NormalizedTicket, TicketStatus, UtilityResponse
from tn811.pdf.extractor import ExtractedFields, parse_date
from tn811.portal.detail import DetailRecord
from tn811.portal.search import TicketRow

logger = logging.getLogger(__name__)

# TN811 ticket numbers: TN + 8-digit date + dash + 6 digits
# e.g. TN20240101-123456
_TICKET_NUM_PATTERN = re.compile(r"^TN\d{8}-\d{6}$", re.IGNORECASE)


def normalize_ticket(
    row: TicketRow,
    detail: DetailRecord | None,
    extracted: ExtractedFields | None,
    pdf_path: str | None = None,
    pdf_sha256: str | None = None,
    now: datetime | None = None,
) -> NormalizedTicket:
    """
    Produce a NormalizedTicket from the three source layers.

    Merge priority (highest wins):
      PDF extracted fields > Detail page fields > Search result row fields

    Args:
        row:        Raw search result row (always present).
        detail:     Parsed detail page record (may be None if skipped).
        extracted:  Fields extracted from PDF text (may be None).
        pdf_path:   Local path to downloaded PDF.
        pdf_sha256: SHA-256 of the downloaded PDF.
        now:        Timestamp for parsed_at (defaults to UTC now).

    Returns:
        NormalizedTicket with all available fields populated.
    """
    now = now or datetime.now(timezone.utc)

    # ── Ticket number ────────────────────────────────────────────────────────
    ticket_number = _best_str(
        extracted.ticket_number if extracted else None,
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
        extracted.call_date if extracted else None,
        detail.fields.get("call_date") if detail else None,
        row.call_date_raw,
    )
    legal_start_date = _parse_date_layered(
        extracted.legal_start_date if extracted else None,
        detail.fields.get("legal_start_date") if detail else None,
        None,
    )
    expiration_date = _parse_date_layered(
        extracted.expiration_date if extracted else None,
        detail.fields.get("expiration_date") if detail else None,
        row.expiration_date_raw,
    )

    # ── Parties ───────────────────────────────────────────────────────────────
    excavator_name = _clean_str(_best_str(
        extracted.excavator_name if extracted else None,
        detail.fields.get("excavator_name") if detail else None,
        row.excavator_name_raw,
    ))
    caller_name = _clean_str(_best_str(
        extracted.caller_name if extracted else None,
        detail.fields.get("caller_name") if detail else None,
    ))
    caller_phone = _clean_str(_best_str(
        extracted.caller_phone if extracted else None,
        detail.fields.get("caller_phone") if detail else None,
    ))

    # ── Work details ──────────────────────────────────────────────────────────
    work_type = _clean_str(_best_str(
        extracted.work_type if extracted else None,
        detail.fields.get("work_type") if detail else None,
        row.work_type_raw,
    ))
    location_text = _clean_str(_best_str(
        extracted.location_text if extracted else None,
        detail.fields.get("location_text") if detail else None,
    ))
    remarks = _clean_str(_best_str(
        extracted.remarks if extracted else None,
        detail.fields.get("remarks") if detail else None,
    ))
    done_for = _clean_str(_best_str(
        extracted.done_for if extracted else None,
        detail.fields.get("done_for") if detail else None,
    ))

    # ── Utilities ─────────────────────────────────────────────────────────────
    utility_references = (extracted.utility_references if extracted else []) or []
    utility_codes = (detail.utility_codes if detail else []) or []

    # ── Cancellation ──────────────────────────────────────────────────────────
    is_cancelled = bool(
        (extracted and extracted.is_cancelled)
        or (detail and detail.is_cancelled)
        or _looks_cancelled(row.status_raw)
    )

    # ── Source metadata ───────────────────────────────────────────────────────
    source_html_path = detail.html_snapshot_path if detail else None
    parse_method = (extracted.extraction_method if extracted else None) or (
        "html" if detail else "row_only"
    )

    # ── Raw text: prefer detail HTML page text; PDF text is not stored here ──
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
        utility_references=utility_references,
        utility_responses=[],
        utility_codes=utility_codes,
        is_cancelled=is_cancelled,
        source_pdf_path=pdf_path,
        source_pdf_sha256=pdf_sha256,
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
    """Return True if the ticket number matches the expected TN811 format."""
    return bool(_TICKET_NUM_PATTERN.match(ticket_number))


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


def _parse_date_layered(
    primary: date | None,
    *fallbacks: str | None,
) -> date | None:
    """
    Return the best available date, trying parsed fallbacks in order.
    """
    if primary is not None:
        return primary
    for raw in fallbacks:
        if raw:
            parsed = parse_date(raw)
            if parsed:
                return parsed
    return None


def _looks_cancelled(status_raw: str | None) -> bool:
    if not status_raw:
        return False
    return "cancel" in status_raw.lower() or "void" in status_raw.lower()
