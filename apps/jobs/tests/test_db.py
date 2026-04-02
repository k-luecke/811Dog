"""
tests/test_db.py — Tests for the database layer.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tn811.db import get_session, health_check, init_db
from tn811.models import ORMTicket, ORMTicketSnapshot, ORMScrapeRun


NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


class TestInitDb:
    def test_init_creates_tables(self, in_memory_db):
        # Tables should exist after init
        from tn811.db import get_engine
        from sqlalchemy import inspect
        inspector = inspect(get_engine())
        tables = inspector.get_table_names()
        assert "tickets" in tables
        assert "ticket_snapshots" in tables
        assert "reminder_events" in tables
        assert "scrape_runs" in tables
        assert "parse_failures" in tables

    def test_init_is_idempotent(self, in_memory_db):
        # Calling init_db again should not raise
        init_db(in_memory_db)


class TestHealthCheck:
    def test_returns_true_when_healthy(self, in_memory_db):
        assert health_check() is True


class TestGetSession:
    def test_can_insert_and_query(self, in_memory_db):
        with get_session() as session:
            ticket = ORMTicket(
                ticket_number="TN20240601-100001",
                county="Davidson County",
                state="TN",
                status="active",
                is_cancelled=False,
                relevance_score=0.9,
                is_relevant=True,
                created_at=NOW,
                updated_at=NOW,
            )
            session.add(ticket)

        with get_session() as session:
            found = session.query(ORMTicket).filter_by(
                ticket_number="TN20240601-100001"
            ).first()
            assert found is not None
            assert found.county == "Davidson County"
            assert found.relevance_score == 0.9

    def test_rollback_on_exception(self, in_memory_db):
        try:
            with get_session() as session:
                ticket = ORMTicket(
                    ticket_number="TN20240601-ROLLBACK",
                    county="Davidson County",
                    state="TN",
                    status="active",
                    is_cancelled=False,
                    relevance_score=0.5,
                    is_relevant=False,
                    created_at=NOW,
                    updated_at=NOW,
                )
                session.add(ticket)
                raise ValueError("Forced rollback")
        except ValueError:
            pass

        with get_session() as session:
            found = session.query(ORMTicket).filter_by(
                ticket_number="TN20240601-ROLLBACK"
            ).first()
            assert found is None

    def test_snapshot_relationship(self, in_memory_db):
        with get_session() as session:
            ticket = ORMTicket(
                ticket_number="TN20240601-100002",
                county="Davidson County",
                state="TN",
                status="active",
                is_cancelled=False,
                relevance_score=0.8,
                is_relevant=True,
                created_at=NOW,
                updated_at=NOW,
            )
            session.add(ticket)
            session.flush()

            snap = ORMTicketSnapshot(
                ticket_id=ticket.id,
                content_hash="abc123",
                payload={"ticket_number": "TN20240601-100002"},
                captured_at=NOW,
            )
            session.add(snap)

        with get_session() as session:
            ticket = session.query(ORMTicket).filter_by(
                ticket_number="TN20240601-100002"
            ).first()
            assert len(ticket.snapshots) == 1
            assert ticket.snapshots[0].content_hash == "abc123"

    def test_unique_constraint_on_snapshot_hash(self, in_memory_db):
        from sqlalchemy.exc import IntegrityError

        with get_session() as session:
            ticket = ORMTicket(
                ticket_number="TN20240601-100003",
                county="Davidson County",
                state="TN",
                status="active",
                is_cancelled=False,
                relevance_score=0.5,
                is_relevant=False,
                created_at=NOW,
                updated_at=NOW,
            )
            session.add(ticket)
            session.flush()
            tid = ticket.id

            snap1 = ORMTicketSnapshot(
                ticket_id=tid,
                content_hash="same_hash",
                payload={},
                captured_at=NOW,
            )
            session.add(snap1)

        with pytest.raises(Exception):  # IntegrityError or similar
            with get_session() as session:
                snap2 = ORMTicketSnapshot(
                    ticket_id=tid,
                    content_hash="same_hash",
                    payload={},
                    captured_at=NOW,
                )
                session.add(snap2)
