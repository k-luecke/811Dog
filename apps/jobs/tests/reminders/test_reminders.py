"""
tests/reminders/test_reminders.py — Tests for reminder eligibility and email rendering.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from tn811.models import NormalizedTicket, TicketStatus
from tn811.reminders.rules import (
    filter_eligible_tickets,
    get_target_expiration_date,
    is_eligible_for_reminder,
)
from tn811.reminders.templates import render_subject, render_plain_text, render_html


def _ticket(
    num: str = "TN20240601-100001",
    expiration: date = date(2099, 1, 1),
    is_gfiber: bool = True,
    status: TicketStatus = TicketStatus.ACTIVE,
    is_cancelled: bool = False,
) -> NormalizedTicket:
    t = NormalizedTicket(
        ticket_number=num,
        county="Davidson County",
        expiration_date=expiration,
        is_gfiber_related=is_gfiber,
        is_cancelled=is_cancelled,
    )
    # Force status since model_validator will re-derive it
    object.__setattr__(t, "status", status)
    return t


TARGET = date(2024, 6, 16)


class TestIsEligibleForReminder:
    def test_eligible_gfiber_active_matching_date(self):
        t = _ticket(expiration=TARGET, is_gfiber=True, status=TicketStatus.ACTIVE)
        assert is_eligible_for_reminder(t, TARGET) is True

    def test_not_eligible_wrong_date(self):
        t = _ticket(expiration=date(2024, 6, 20), is_gfiber=True, status=TicketStatus.ACTIVE)
        assert is_eligible_for_reminder(t, TARGET) is False

    def test_not_eligible_non_gfiber(self):
        t = _ticket(expiration=TARGET, is_gfiber=False, status=TicketStatus.ACTIVE)
        assert is_eligible_for_reminder(t, TARGET) is False

    def test_not_eligible_cancelled(self):
        t = _ticket(expiration=TARGET, is_gfiber=True, status=TicketStatus.CANCELLED)
        assert is_eligible_for_reminder(t, TARGET) is False

    def test_not_eligible_expired(self):
        t = _ticket(expiration=TARGET, is_gfiber=True, status=TicketStatus.EXPIRED)
        assert is_eligible_for_reminder(t, TARGET) is False

    def test_not_eligible_no_expiration(self):
        t = NormalizedTicket(
            ticket_number="TN20240601-999999",
            county="Davidson County",
            is_gfiber_related=True,
        )
        assert is_eligible_for_reminder(t, TARGET) is False


class TestFilterEligibleTickets:
    def test_filters_correct_tickets(self):
        tickets = [
            _ticket("TN20240601-100001", expiration=TARGET, is_gfiber=True, status=TicketStatus.ACTIVE),
            _ticket("TN20240601-100002", expiration=date(2024, 6, 20), is_gfiber=True, status=TicketStatus.ACTIVE),
            _ticket("TN20240601-100003", expiration=TARGET, is_gfiber=False, status=TicketStatus.ACTIVE),
            _ticket("TN20240601-100004", expiration=TARGET, is_gfiber=True, status=TicketStatus.CANCELLED),
        ]
        eligible = filter_eligible_tickets(tickets, TARGET)
        assert len(eligible) == 1
        assert eligible[0].ticket_number == "TN20240601-100001"

    def test_empty_list(self):
        assert filter_eligible_tickets([], TARGET) == []

    def test_all_eligible(self):
        tickets = [
            _ticket(f"TN20240601-10000{i}", expiration=TARGET, is_gfiber=True, status=TicketStatus.ACTIVE)
            for i in range(5)
        ]
        eligible = filter_eligible_tickets(tickets, TARGET)
        assert len(eligible) == 5


class TestGetTargetExpirationDate:
    def test_returns_today_plus_lead_days(self, base_config):
        today = date(2024, 6, 12)
        target = get_target_expiration_date(base_config.reminders, today=today)
        assert target == date(2024, 6, 16)  # 12 + 4 lead days


class TestRenderSubject:
    def test_singular(self):
        subj = render_subject(1, 4)
        assert "1" in subj
        assert "ticket" in subj
        assert "4" in subj

    def test_plural(self):
        subj = render_subject(3, 4)
        assert "3" in subj
        assert "tickets" in subj


class TestRenderPlainText:
    def test_contains_ticket_number(self):
        t = _ticket("TN20240601-100001", expiration=TARGET)
        plain = render_plain_text([t], TARGET, 4)
        assert "TN20240601-100001" in plain

    def test_contains_expiration_date(self):
        t = _ticket(expiration=TARGET)
        plain = render_plain_text([t], TARGET, 4)
        assert "2024" in plain

    def test_contains_county(self):
        t = _ticket(expiration=TARGET)
        plain = render_plain_text([t], TARGET, 4)
        assert "Davidson" in plain

    def test_non_empty(self):
        t = _ticket(expiration=TARGET)
        plain = render_plain_text([t], TARGET, 4)
        assert len(plain) > 100


class TestRenderHtml:
    def test_valid_html_structure(self):
        t = _ticket("TN20240601-100001", expiration=TARGET)
        html = render_html([t], TARGET, 4)
        assert "<html" in html
        assert "</html>" in html
        assert "TN20240601-100001" in html

    def test_no_xss_in_ticket_number(self):
        t = _ticket("<script>alert(1)</script>", expiration=TARGET)
        html = render_html([t], TARGET, 4)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
