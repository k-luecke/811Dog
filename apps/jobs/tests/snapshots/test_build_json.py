"""
tests/snapshots/test_build_json.py — Tests for dashboard JSON generation.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tn811.db import get_session
from tn811.models import ORMTicket
from tn811.snapshots.build_dashboard_json import build_all

NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _insert_ticket(session, num, county, status, is_relevant, expiration="2099-12-31"):
    t = ORMTicket(
        ticket_number=num,
        county=county,
        state="TN",
        status=status,
        is_cancelled=(status == "cancelled"),
        relevance_score=0.9 if is_relevant else 0.1,
        relevance_reasons=["northstar_fiber"] if is_relevant else [],
        is_relevant=is_relevant,
        expiration_date=expiration,
        work_type="Fiber Install" if is_relevant else "Water",
        excavator_name="Acme Fiber" if is_relevant else "Metro Water",
        probable_work_group="Acme Fiber" if is_relevant else None,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(t)


class TestBuildAll:
    def test_summary_json_created(self, in_memory_db, tmp_path):
        with get_session() as session:
            _insert_ticket(session, "TN20240601-100001", "Davidson County", "active", True)
            _insert_ticket(session, "TN20240601-100002", "Davidson County", "active", False)

        in_memory_db.paths.exports_dir = str(tmp_path / "exports")

        with get_session() as session:
            build_all(session, in_memory_db)

        summary_path = tmp_path / "exports" / "summary.json"
        assert summary_path.exists()
        data = json.loads(summary_path.read_text())
        assert "total_tickets" in data
        assert data["total_tickets"] == 2
        assert data["relevant"] == 1

    def test_tickets_active_json_created(self, in_memory_db, tmp_path):
        with get_session() as session:
            _insert_ticket(session, "TN20240601-100001", "Davidson County", "active", True)

        in_memory_db.paths.exports_dir = str(tmp_path / "exports")

        with get_session() as session:
            build_all(session, in_memory_db)

        path = tmp_path / "exports" / "tickets_active.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["count"] == 1
        assert data["tickets"][0]["ticket_number"] == "TN20240601-100001"

    def test_tickets_expiring_json_created(self, in_memory_db, tmp_path):
        with get_session() as session:
            # Expiring in 5 days from "today" (no easy way to mock today without DI)
            _insert_ticket(
                session, "TN20240601-100001", "Davidson County", "active", True,
                expiration="2024-06-06",  # will be "expiring" if run in 2024
            )

        in_memory_db.paths.exports_dir = str(tmp_path / "exports")

        with get_session() as session:
            build_all(session, in_memory_db)

        path = tmp_path / "exports" / "tickets_expiring.json"
        assert path.exists()

    def test_by_county_json_created(self, in_memory_db, tmp_path):
        with get_session() as session:
            _insert_ticket(session, "TN20240601-100001", "Davidson County", "active", True)
            _insert_ticket(session, "TN20240601-100002", "Rutherford County", "active", True)

        in_memory_db.paths.exports_dir = str(tmp_path / "exports")

        with get_session() as session:
            build_all(session, in_memory_db)

        path = tmp_path / "exports" / "by_county.json"
        assert path.exists()
        data = json.loads(path.read_text())
        county_names = [c["county"] for c in data["counties"]]
        assert "Davidson County" in county_names
        assert "Rutherford County" in county_names

    def test_by_work_group_json_created(self, in_memory_db, tmp_path):
        with get_session() as session:
            _insert_ticket(session, "TN20240601-100001", "Davidson County", "active", True)

        in_memory_db.paths.exports_dir = str(tmp_path / "exports")

        with get_session() as session:
            build_all(session, in_memory_db)

        path = tmp_path / "exports" / "by_work_group.json"
        assert path.exists()

    def test_parse_failures_json_created(self, in_memory_db, tmp_path):
        in_memory_db.paths.exports_dir = str(tmp_path / "exports")

        with get_session() as session:
            build_all(session, in_memory_db)

        path = tmp_path / "exports" / "parse_failures.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert "failures" in data

    def test_all_json_files_valid(self, in_memory_db, tmp_path):
        with get_session() as session:
            _insert_ticket(session, "TN20240601-100001", "Davidson County", "active", True)

        in_memory_db.paths.exports_dir = str(tmp_path / "exports")

        with get_session() as session:
            build_all(session, in_memory_db)

        expected = [
            "summary.json", "tickets_active.json", "tickets_expiring.json",
            "by_county.json", "by_work_group.json", "recent_changes.json",
            "parse_failures.json",
        ]
        for fname in expected:
            path = tmp_path / "exports" / fname
            assert path.exists(), f"Missing: {fname}"
            # Ensure valid JSON
            json.loads(path.read_text())

    def test_generated_at_field_present(self, in_memory_db, tmp_path):
        in_memory_db.paths.exports_dir = str(tmp_path / "exports")
        with get_session() as session:
            build_all(session, in_memory_db)

        for fname in ["summary.json", "tickets_active.json"]:
            data = json.loads((tmp_path / "exports" / fname).read_text())
            assert "generated_at" in data
