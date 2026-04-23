"""
tests/pdf/test_normalize.py — Tests for the ticket normalization pipeline.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from tn811.models import NormalizedTicket, TicketStatus
from tn811.normalize.tickets import normalize_ticket, validate_ticket_number
from tn811.portal.detail import DetailRecord
from tn811.portal.search import TicketRow


def _make_row(**kwargs) -> TicketRow:
    defaults = dict(
        ticket_number="TN20240601-100001",
        county="Davidson County",
        detail_url="https://tn811.com/ticket/detail/TN20240601-100001",
    )
    defaults.update(kwargs)
    return TicketRow(**defaults)


def _make_detail(**kwargs) -> DetailRecord:
    defaults = dict(
        ticket_number="TN20240601-100001",
        county="Davidson County",
        fields={
            "ticket_number": "TN20240601-100001",
            "county": "Davidson County",
            "call_date": "06/01/2024",
            "expiration_date": "06/16/2024",
            "excavator_name": "Acme Fiber LLC",
            "work_type": "Telecom - Underground Fiber",
            "location_text": "123 Main St Nashville TN",
            "remarks": "Google Fiber drop bury",
        },
    )
    defaults.update(kwargs)
    return DetailRecord(**defaults)


NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


class TestNormalizeTicket:
    def test_returns_normalized_ticket(self):
        row = _make_row()
        t = normalize_ticket(row, None, now=NOW)
        assert isinstance(t, NormalizedTicket)

    def test_ticket_number_uppercased(self):
        row = _make_row(ticket_number="tn20240601-100001")
        t = normalize_ticket(row, None, now=NOW)
        assert t.ticket_number == "TN20240601-100001"

    def test_detail_data_preferred_over_row(self):
        row = _make_row(excavator_name_raw="Row Corp")
        detail = _make_detail(fields={"excavator_name": "Detail Corp"})
        t = normalize_ticket(row, detail, now=NOW)
        assert t.excavator_name == "Detail Corp"

    def test_row_data_used_as_fallback(self):
        row = _make_row(excavator_name_raw="Row Corp")
        t = normalize_ticket(row, None, now=NOW)
        assert t.excavator_name == "Row Corp"

    def test_expiration_date_parsed_from_detail(self):
        detail = _make_detail(fields={"expiration_date": "06/16/2024"})
        t = normalize_ticket(_make_row(), detail, now=NOW)
        assert t.expiration_date == date(2024, 6, 16)

    def test_expiration_date_parsed_from_row(self):
        row = _make_row(expiration_date_raw="06/16/2024")
        t = normalize_ticket(row, None, now=NOW)
        assert t.expiration_date == date(2024, 6, 16)

    def test_active_status_derived(self):
        row = _make_row(expiration_date_raw="12/31/2099")
        t = normalize_ticket(row, None, now=NOW)
        assert t.status == TicketStatus.ACTIVE

    def test_expired_status_derived(self):
        row = _make_row(expiration_date_raw="01/01/2000")
        t = normalize_ticket(row, None, now=NOW)
        assert t.status == TicketStatus.EXPIRED

    def test_cancelled_from_detail(self):
        row = _make_row()
        detail = _make_detail(is_cancelled=True)
        t = normalize_ticket(row, detail, now=NOW)
        assert t.is_cancelled is True
        assert t.status == TicketStatus.CANCELLED

    def test_cancelled_from_row_status(self):
        row = _make_row(status_raw="Cancelled")
        t = normalize_ticket(row, None, now=NOW)
        assert t.is_cancelled is True

    def test_parsed_at_set(self):
        row = _make_row()
        t = normalize_ticket(row, None, now=NOW)
        assert t.parsed_at == NOW

    def test_whitespace_collapsed_in_fields(self):
        detail = _make_detail(fields={"excavator_name": "  Acme   Fiber   LLC  "})
        t = normalize_ticket(_make_row(), detail, now=NOW)
        assert t.excavator_name == "Acme Fiber LLC"

    def test_content_hash_stable(self):
        row = _make_row()
        t1 = normalize_ticket(row, None, now=NOW)
        t2 = normalize_ticket(row, None, now=NOW)
        assert t1.content_hash() == t2.content_hash()

    def test_content_hash_changes_on_different_data(self):
        d1 = _make_detail(fields={"excavator_name": "Company A"})
        d2 = _make_detail(fields={"excavator_name": "Company B"})
        t1 = normalize_ticket(_make_row(), d1, now=NOW)
        t2 = normalize_ticket(_make_row(), d2, now=NOW)
        assert t1.content_hash() != t2.content_hash()


class TestValidateTicketNumber:
    def test_valid_integer_format(self):
        assert validate_ticket_number("2609013987") is True

    def test_valid_legacy_format(self):
        assert validate_ticket_number("TN20240601-100001") is True

    def test_lowercase_legacy_valid(self):
        assert validate_ticket_number("tn20240601-100001") is True

    def test_missing_dash_invalid(self):
        assert validate_ticket_number("TN20240601100001") is False

    def test_wrong_prefix_invalid(self):
        assert validate_ticket_number("GA20240601-100001") is False

    def test_empty_invalid(self):
        assert validate_ticket_number("") is False
