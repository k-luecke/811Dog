"""
reminders/rules.py — Eligibility rules for ticket expiry reminders.

Determines which tickets qualify for a reminder email based on:
- Expiration date is exactly lead_days from today (in the configured timezone)
- Ticket is relevant
- Ticket is not cancelled or already expired
- No reminder has already been sent for this ticket+expiration+lead_days combo

This module is pure (no DB access). The caller (cli.py) handles DB checks.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

import pytz

from tn811.config import RemindersConfig
from tn811.models import NormalizedTicket, TicketStatus

logger = logging.getLogger(__name__)


def get_target_expiration_date(config: RemindersConfig, today: date | None = None) -> date:
    """
    Return the expiration date that qualifies for a reminder today.

    A ticket qualifies if its expiration_date == today + lead_days.

    Args:
        config: Reminders configuration.
        today:  Override for today's date (for testing). Defaults to now in
                the configured timezone.

    Returns:
        The target expiration date.
    """
    if today is None:
        tz = pytz.timezone(config.timezone)
        today = datetime.now(tz).date()
    from datetime import timedelta
    return today + timedelta(days=config.lead_days)


def is_eligible_for_reminder(
    ticket: NormalizedTicket,
    target_expiration: date,
) -> bool:
    """
    Return True if this ticket should receive a reminder.

    Eligibility criteria:
    1. is_relevant (only send reminders for relevant tickets)
    2. expiration_date == target_expiration
    3. status is ACTIVE (not cancelled, not already expired)
    4. expiration_date is not None
    """
    if not ticket.is_relevant:
        return False

    if ticket.expiration_date is None:
        return False

    if ticket.expiration_date != target_expiration:
        return False

    if ticket.status in (TicketStatus.CANCELLED, TicketStatus.EXPIRED):
        return False

    return True


def filter_eligible_tickets(
    tickets: list[NormalizedTicket],
    target_expiration: date,
) -> list[NormalizedTicket]:
    """
    Filter a list of tickets to those eligible for a reminder.

    Args:
        tickets:           All relevant active tickets.
        target_expiration: The expiration date to match.

    Returns:
        Subset of tickets that qualify.
    """
    eligible = [t for t in tickets if is_eligible_for_reminder(t, target_expiration)]
    logger.info(
        "Reminder eligibility filter",
        extra={
            "total": len(tickets),
            "eligible": len(eligible),
            "target_expiration": target_expiration.isoformat(),
        },
    )
    return eligible
