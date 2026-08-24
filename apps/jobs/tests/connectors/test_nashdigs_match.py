"""
Tests for the NashDigs spatial match.

Uses real shapely/pyproj — no mocking. Runs against in_memory_db with
synthetic ORMTicket rows plus a tiny ORMNashDigsProject fixture. All
coordinates are in Nashville so UTM-16N math works end-to-end.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tn811.connectors.nashdigs_match import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_NONE,
    TIER_HIGH_MAX_M,
    TIER_LOW_MAX_M,
    TIER_MEDIUM_MAX_M,
    classify_distance,
    match_all_relevant_tickets,
)
from tn811.models import ORMNashDigsProject, ORMTicket


# A short fake BNA### fiber segment near a real Nashville point. A line at
# lat=36.100, lon from -86.730 to -86.729 is ~90 m long at this latitude.
FAKE_PACKAGE_GEOJSON = {
    "type": "MultiLineString",
    "coordinates": [[[-86.730, 36.100], [-86.729, 36.100]]],
}


def _seed_package(session, name: str = "BNA100a", project_id: int = 100) -> None:
    now = datetime.now(timezone.utc)
    session.add(
        ORMNashDigsProject(
            project_id=project_id,
            project_name=name,
            facility_type="Communication",
            project_type="Underground",
            description=None,
            owner="Google",
            department="Google",
            division="Google",
            status="Started",
            council_district="22",
            est_start_date="2026-04-01",
            est_end_date="2026-06-01",
            act_start_date="2026-04-01",
            act_end_date="2026-06-01",
            geometry_geojson=FAKE_PACKAGE_GEOJSON,
            fetched_at=now,
            created_at=now,
        )
    )


def _seed_ticket(
    session,
    *,
    ticket_number: str,
    latitude: float | None,
    longitude: float | None,
    is_relevant: bool = True,
) -> None:
    now = datetime.now(timezone.utc)
    session.add(
        ORMTicket(
            ticket_number=ticket_number,
            county="Davidson County",
            state="TN",
            call_date="2026-04-20",
            expiration_date="2026-05-01",
            excavator_name="TEST",
            caller_name="Test",
            caller_phone="",
            work_type="FIBER",
            location_text="",
            intersection_text="",
            done_for="NORTHSTAR FIBER",
            utility_statuses=[],
            utility_codes=[],
            utility_references=[],
            utility_responses=[],
            is_ready_to_dig=False,
            has_late_utility=False,
            late_utility_codes=[],
            pending_utility_codes=[],
            blocking_utility_codes=[],
            status="active",
            is_cancelled=False,
            relevance_score=1.0,
            is_relevant=is_relevant,
            latitude=latitude,
            longitude=longitude,
            created_at=now,
            updated_at=now,
            latest_content_hash=ticket_number,
        )
    )


# ── classify_distance ────────────────────────────────────────────────────────


class TestClassifyDistance:
    def test_high_tier_upper_bound(self):
        assert classify_distance(0.0) == CONFIDENCE_HIGH
        assert classify_distance(TIER_HIGH_MAX_M) == CONFIDENCE_HIGH

    def test_medium_tier(self):
        assert classify_distance(TIER_HIGH_MAX_M + 0.1) == CONFIDENCE_MEDIUM
        assert classify_distance(TIER_MEDIUM_MAX_M) == CONFIDENCE_MEDIUM

    def test_low_tier(self):
        assert classify_distance(TIER_MEDIUM_MAX_M + 0.1) == CONFIDENCE_LOW
        assert classify_distance(TIER_LOW_MAX_M) == CONFIDENCE_LOW

    def test_none_tier(self):
        assert classify_distance(TIER_LOW_MAX_M + 0.1) == CONFIDENCE_NONE
        assert classify_distance(500.0) == CONFIDENCE_NONE


# ── End-to-end match against in_memory_db ────────────────────────────────────


class TestMatchEndToEnd:
    def test_ticket_on_line_is_high_confidence(self, in_memory_db):
        from tn811.db import get_session
        with get_session() as session:
            _seed_package(session)
            # Point (36.100, -86.7295) is directly on the line — distance ≈ 0
            _seed_ticket(session, ticket_number="T_ON",
                         latitude=36.100, longitude=-86.7295)

        with get_session() as session:
            stats = match_all_relevant_tickets(session)

        assert stats.high == 1
        assert stats.medium == stats.low == stats.none == 0
        with get_session() as session:
            row = session.query(ORMTicket).filter_by(ticket_number="T_ON").one()
            assert row.nashdigs_project_name == "BNA100a"
            assert row.nashdigs_match_confidence == CONFIDENCE_HIGH
            assert row.nashdigs_distance_m is not None
            assert row.nashdigs_distance_m < 5.0

    def test_medium_and_low_bands(self, in_memory_db):
        from tn811.db import get_session
        # ~50 m north of the line at our test latitude requires +0.00045 deg lat
        # ~100 m north requires +0.0009 deg lat
        # ~200 m north requires +0.0018 deg lat
        with get_session() as session:
            _seed_package(session)
            _seed_ticket(session, ticket_number="T_MED",
                         latitude=36.100 + 0.00045, longitude=-86.7295)
            _seed_ticket(session, ticket_number="T_LOW",
                         latitude=36.100 + 0.0009, longitude=-86.7295)
            _seed_ticket(session, ticket_number="T_NONE",
                         latitude=36.100 + 0.0018, longitude=-86.7295)

        with get_session() as session:
            match_all_relevant_tickets(session)

        with get_session() as session:
            by_num = {
                r.ticket_number: r
                for r in session.query(ORMTicket).all()
            }
        assert by_num["T_MED"].nashdigs_match_confidence == CONFIDENCE_MEDIUM
        assert by_num["T_MED"].nashdigs_project_name == "BNA100a"
        assert by_num["T_LOW"].nashdigs_match_confidence == CONFIDENCE_LOW
        assert by_num["T_LOW"].nashdigs_project_name == "BNA100a"
        assert by_num["T_NONE"].nashdigs_match_confidence == CONFIDENCE_NONE
        # 'none' tier must NOT carry a project name
        assert by_num["T_NONE"].nashdigs_project_name is None

    def test_ticket_without_coords_is_skipped(self, in_memory_db):
        from tn811.db import get_session
        with get_session() as session:
            _seed_package(session)
            _seed_ticket(session, ticket_number="T_NOCOORD",
                         latitude=None, longitude=None)

        with get_session() as session:
            stats = match_all_relevant_tickets(session)
        assert stats.skipped_no_coords == 1
        assert stats.high == 0

    def test_non_relevant_ticket_is_not_matched(self, in_memory_db):
        from tn811.db import get_session
        with get_session() as session:
            _seed_package(session)
            _seed_ticket(session, ticket_number="T_NONGF",
                         latitude=36.100, longitude=-86.7295, is_relevant=False)

        with get_session() as session:
            stats = match_all_relevant_tickets(session)
        # No relevant tickets → total=0 from this test's perspective
        assert stats.total == 0

    def test_dry_run_does_not_write(self, in_memory_db):
        from tn811.db import get_session
        with get_session() as session:
            _seed_package(session)
            _seed_ticket(session, ticket_number="T_DRY",
                         latitude=36.100, longitude=-86.7295)

        with get_session() as session:
            stats = match_all_relevant_tickets(session, dry_run=True)
        assert stats.high == 1
        with get_session() as session:
            row = session.query(ORMTicket).filter_by(ticket_number="T_DRY").one()
            assert row.nashdigs_project_name is None
            assert row.nashdigs_match_confidence is None

    def test_empty_package_cache_warns_and_skips(self, in_memory_db):
        from tn811.db import get_session
        with get_session() as session:
            _seed_ticket(session, ticket_number="T_ONLY",
                         latitude=36.100, longitude=-86.7295)

        with get_session() as session:
            stats = match_all_relevant_tickets(session)
        assert stats.skipped_no_packages == 1
        with get_session() as session:
            row = session.query(ORMTicket).filter_by(ticket_number="T_ONLY").one()
            assert row.nashdigs_project_name is None  # unchanged
