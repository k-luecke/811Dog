"""
Tests for the NashDigs connector.

Uses httpx.MockTransport so no network is hit during tests. A single canned
GeoJSON response covers parse + upsert + load_cached paths. End-to-end upsert
idempotency and stale-deletion are exercised against the in-memory SQLite.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import httpx
import pytest

from tn811.connectors.nashdigs import (
    DEFAULT_WHERE,
    LINES_QUERY_URL,
    NashDigsPackage,
    fetch_target_packages,
    load_cached,
    upsert_packages,
)
from tn811.models import ORMNashDigsProject


# ── Canned GeoJSON fixtures ───────────────────────────────────────────────────


def _geojson(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def _feat(
    project_id: int,
    name: str,
    *,
    status: str = "Started",
    act_start_ms: int | None = 1762_000_000_000,  # ~2025-11-01
    act_end_ms: int | None = 1767_000_000_000,
    owner: str = "Northstar",
    description: str | None = "",
    coords: list[list[list[float]]] | None = None,
) -> dict:
    coords = coords or [[[-86.73, 36.10], [-86.73, 36.101]]]
    return {
        "type": "Feature",
        "geometry": {"type": "MultiLineString", "coordinates": coords},
        "properties": {
            "ProjectID": project_id,
            "ProjectName": name,
            "FacilityType": "Communication",
            "ProjectType": "Underground",
            "Description": description,
            "Owner": owner,
            "Department": owner,
            "Division": owner,
            "Status": status,
            "CouncilDistrict": "22",
            "EstStartDate": act_start_ms,
            "EstEndDate": act_end_ms,
            "ActStartDate": act_start_ms,
            "ActEndDate": act_end_ms,
        },
    }


@pytest.fixture
def two_feature_response() -> dict:
    return _geojson([
        _feat(49701, "BNA104a", status="Started"),
        _feat(51447, "BNA111e-013", status="Committed"),
    ])


def _mock_client(payload: dict) -> httpx.Client:
    """Build an httpx.Client that answers any GET with the given payload."""
    def handler(request: httpx.Request) -> httpx.Response:
        # Sanity-check a couple of expected params made it through
        assert "Owner" in request.url.query.decode("utf-8") or "OWNER" in request.url.query.decode("utf-8").upper()
        return httpx.Response(200, json=payload)
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport, timeout=5.0)


# ── Parsing ───────────────────────────────────────────────────────────────────


class TestParse:
    def test_fetch_returns_packages(self, two_feature_response):
        with _mock_client(two_feature_response) as c:
            pkgs = fetch_target_packages(client=c)
        assert len(pkgs) == 2
        assert {p.project_name for p in pkgs} == {"BNA104a", "BNA111e-013"}

    def test_status_and_owner_preserved(self, two_feature_response):
        with _mock_client(two_feature_response) as c:
            pkgs = fetch_target_packages(client=c)
        by_name = {p.project_name: p for p in pkgs}
        assert by_name["BNA104a"].status == "Started"
        assert by_name["BNA111e-013"].status == "Committed"
        for p in pkgs:
            assert p.owner == "Northstar"
            assert p.facility_type == "Communication"
            assert p.project_type == "Underground"

    def test_dates_parsed_from_ms_epoch(self, two_feature_response):
        with _mock_client(two_feature_response) as c:
            pkgs = fetch_target_packages(client=c)
        p = pkgs[0]
        # 1762000000000 ms = 2025-11-01 12:26:40Z → date 2025-11-01
        assert p.act_start_date == date(2025, 11, 1)
        assert isinstance(p.act_end_date, date)

    def test_missing_dates_are_none(self):
        payload = _geojson([_feat(1, "BNA100a", act_start_ms=None, act_end_ms=None)])
        with _mock_client(payload) as c:
            pkgs = fetch_target_packages(client=c)
        p = pkgs[0]
        assert p.act_start_date is None
        assert p.act_end_date is None
        assert p.est_start_date is None

    def test_geometry_preserved_as_geojson(self, two_feature_response):
        with _mock_client(two_feature_response) as c:
            pkgs = fetch_target_packages(client=c)
        geom = pkgs[0].geometry_geojson
        assert geom["type"] == "MultiLineString"
        assert isinstance(geom["coordinates"], list)

    def test_empty_string_description_becomes_none(self):
        payload = _geojson([_feat(1, "BNA100a", description="   ")])
        with _mock_client(payload) as c:
            pkgs = fetch_target_packages(client=c)
        assert pkgs[0].description is None


# ── Upsert + load_cached (hits in_memory_db) ──────────────────────────────────


class TestUpsertIdempotency:
    def test_first_upsert_inserts_all(self, in_memory_db, two_feature_response):
        from tn811.db import get_session
        now = datetime.now(timezone.utc)
        with _mock_client(two_feature_response) as c:
            pkgs = fetch_target_packages(client=c)

        with get_session() as session:
            stats = upsert_packages(session, pkgs, now)

        assert stats == {"new": 2, "updated": 0, "unchanged": 0, "deleted": 0}
        with get_session() as session:
            assert session.query(ORMNashDigsProject).count() == 2

    def test_second_upsert_unchanged(self, in_memory_db, two_feature_response):
        from tn811.db import get_session
        now = datetime.now(timezone.utc)
        with _mock_client(two_feature_response) as c:
            pkgs = fetch_target_packages(client=c)

        with get_session() as session:
            upsert_packages(session, pkgs, now)
        with get_session() as session:
            stats = upsert_packages(session, pkgs, now)

        assert stats["new"] == 0
        assert stats["updated"] == 0
        assert stats["unchanged"] == 2
        assert stats["deleted"] == 0

    def test_changed_record_is_updated(self, in_memory_db):
        """When ActEndDate shifts, the existing row should get the new value."""
        from tn811.db import get_session
        now = datetime.now(timezone.utc)

        before = _geojson([_feat(1, "BNA100a", act_end_ms=1767_000_000_000)])
        with _mock_client(before) as c:
            before_pkgs = fetch_target_packages(client=c)
        with get_session() as session:
            upsert_packages(session, before_pkgs, now)

        after = _geojson([_feat(1, "BNA100a", act_end_ms=1800_000_000_000)])
        with _mock_client(after) as c:
            after_pkgs = fetch_target_packages(client=c)
        with get_session() as session:
            stats = upsert_packages(session, after_pkgs, now)

        assert stats["updated"] == 1
        with get_session() as session:
            row = session.query(ORMNashDigsProject).filter_by(project_id=1).one()
            # 1800000000000 ms = 2027-01-15-ish — just verify it's beyond 2026
            assert row.act_end_date >= "2027-01-01"

    def test_stale_record_is_deleted(self, in_memory_db):
        """A project that drops out of the feed should be removed from cache."""
        from tn811.db import get_session
        now = datetime.now(timezone.utc)

        initial = _geojson([_feat(1, "BNA100a"), _feat(2, "BNA100b")])
        with _mock_client(initial) as c:
            pkgs = fetch_target_packages(client=c)
        with get_session() as session:
            upsert_packages(session, pkgs, now)

        # NashDigs no longer reports project 2 (cancelled, or no longer Northstar)
        reduced = _geojson([_feat(1, "BNA100a")])
        with _mock_client(reduced) as c:
            pkgs = fetch_target_packages(client=c)
        with get_session() as session:
            stats = upsert_packages(session, pkgs, now)

        assert stats["deleted"] == 1
        with get_session() as session:
            remaining = [r.project_name for r in session.query(ORMNashDigsProject).all()]
            assert remaining == ["BNA100a"]


class TestLoadCached:
    def test_roundtrip_preserves_fields(self, in_memory_db, two_feature_response):
        from tn811.db import get_session
        now = datetime.now(timezone.utc)
        with _mock_client(two_feature_response) as c:
            pkgs = fetch_target_packages(client=c)
        with get_session() as session:
            upsert_packages(session, pkgs, now)

        with get_session() as session:
            loaded = load_cached(session)
        by_name = {p.project_name: p for p in loaded}
        assert set(by_name) == {"BNA104a", "BNA111e-013"}
        # Dates survived the ISO-string encode/decode
        assert isinstance(by_name["BNA104a"].act_start_date, date)
        # Geometry survived
        assert by_name["BNA104a"].geometry_geojson["type"] == "MultiLineString"


# ── Endpoint sanity ───────────────────────────────────────────────────────────


def test_endpoint_constants_are_public_and_stable():
    """Catches accidental rename/typo — the URL pattern is contractual."""
    assert "services2.arcgis.com" in LINES_QUERY_URL
    assert "/NashDigs_Project_Layers_view/FeatureServer/1/query" in LINES_QUERY_URL
    assert "NORTHSTAR" in DEFAULT_WHERE.upper()
