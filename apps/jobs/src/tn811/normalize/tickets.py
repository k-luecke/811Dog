"""
normalize/tickets.py — Ticket normalization pipeline.

Takes raw inputs (DetailRecord + TicketRow) and produces a NormalizedTicket
with validated, cleaned fields.

The normalizer is the single place where:
- date strings are parsed into date objects
- text is stripped / truncated
- ticket number format is validated
- status is derived
- utility rollups (is_ready_to_dig, late_utility_codes, etc.) are computed
- detail page fields override search row fields
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from tn811.models import (
    NormalizedTicket,
    UTIL_STATUS_CLEAR,
    UTIL_STATUS_LOCATED,
    UTIL_STATUS_PENDING,
    UTIL_STATUS_DELAYED,
    UTIL_STATUS_NOT_CLEAR,
    _UTIL_STATUSES_READY,
)
from tn811.pdf.extractor import parse_date
from tn811.portal.detail import DetailRecord
from tn811.portal.search import TicketRow

logger = logging.getLogger(__name__)

# Live portal: pure integers (e.g. 2609013987)
_TICKET_NUM_PATTERN = re.compile(r"^\d{10,}$")
# Legacy format from test fixtures: TN20240101-123456
_TICKET_NUM_PATTERN_LEGACY = re.compile(r"^TN\d{8}-\d{6}$", re.IGNORECASE)

_72H = timedelta(hours=72)


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
    intersection_text = _clean_str(_best_str(
        detail.fields.get("intersection_text") if detail else None,
    ))
    remarks = _clean_str(_best_str(
        detail.fields.get("remarks") if detail else None,
        row.remarks_raw,
    ))
    done_for = _clean_str(_best_str(
        detail.fields.get("done_for") if detail else None,
        row.work_done_for_raw,
    ))

    # ── Utilities ─────────────────────────────────────────────────────────────
    utility_codes = (detail.utility_codes if detail else []) or []
    raw_utility_statuses = (detail.utility_statuses if detail else []) or []

    # ── Cancellation ──────────────────────────────────────────────────────────
    is_cancelled = bool(
        (detail and detail.is_cancelled)
        or _looks_cancelled(row.status_raw)
    )

    # ── Utility rollups (requires call_date) ──────────────────────────────────
    utility_statuses, rollups = _compute_utility_rollups(
        raw_utility_statuses, call_date, is_cancelled, now,
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
        intersection_text=intersection_text,
        remarks=remarks,
        done_for=done_for,
        utility_references=[],
        utility_responses=[],
        utility_codes=utility_codes,
        utility_statuses=utility_statuses,
        is_ready_to_dig=rollups["is_ready_to_dig"],
        has_late_utility=rollups["has_late_utility"],
        late_utility_codes=rollups["late_utility_codes"],
        pending_utility_codes=rollups["pending_utility_codes"],
        blocking_utility_codes=rollups["blocking_utility_codes"],
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
            "is_ready_to_dig": ticket.is_ready_to_dig,
            "late_utilities": ticket.late_utility_codes,
        },
    )
    return ticket


def validate_ticket_number(ticket_number: str) -> bool:
    """Return True if the ticket number matches either the live portal format or legacy format."""
    return bool(
        _TICKET_NUM_PATTERN.match(ticket_number)
        or _TICKET_NUM_PATTERN_LEGACY.match(ticket_number)
    )


def _compute_utility_rollups(
    raw_statuses: list[dict[str, Any]],
    call_date: date | None,
    is_cancelled: bool,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Enrich utility status records with is_late and compute ticket-level rollups.

    Args:
        raw_statuses: Utility status records from detail extraction (no is_late yet).
        call_date:    Ticket call date (used for 72-hour late threshold).
        is_cancelled: If True, no utility can be late.
        now:          Current UTC time (defaults to now).

    Returns:
        (enriched_statuses, rollup_dict) where rollup_dict has keys:
            is_ready_to_dig, has_late_utility, late_utility_codes,
            pending_utility_codes, blocking_utility_codes
    """
    now = now or datetime.now(timezone.utc)
    enriched: list[dict[str, Any]] = []

    late: list[str] = []
    pending: list[str] = []
    blocking: list[str] = []

    # Compute cutoff for "late": call_date + 72h, in UTC
    call_cutoff: datetime | None = None
    if call_date and not is_cancelled:
        call_cutoff = datetime(
            call_date.year, call_date.month, call_date.day, tzinfo=timezone.utc
        ) + _72H

    for u in raw_statuses:
        rec = dict(u)
        status = rec.get("status", "unknown")
        code = rec.get("code", "")

        is_late = False
        if status == UTIL_STATUS_PENDING and call_cutoff is not None:
            is_late = now > call_cutoff
        rec["is_late"] = is_late

        if is_late:
            late.append(code)
        elif status == UTIL_STATUS_PENDING:
            pending.append(code)

        if status in (UTIL_STATUS_DELAYED, UTIL_STATUS_NOT_CLEAR):
            blocking.append(code)

        enriched.append(rec)

    is_ready = bool(enriched) and all(
        u.get("status") in _UTIL_STATUSES_READY for u in enriched
    )

    rollups: dict[str, Any] = {
        "is_ready_to_dig": is_ready,
        "has_late_utility": bool(late),
        "late_utility_codes": late,
        "pending_utility_codes": pending,
        "blocking_utility_codes": blocking,
    }
    return enriched, rollups


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
