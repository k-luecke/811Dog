"""
Tests for exporters/kmz_export.py.

The KMZ is a zip containing a doc.kml, so tests crack the zip open and parse
the KML with stdlib xml.etree. No Google Earth runtime is exercised — the
goal is structural + data correctness.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime
from pathlib import Path

import pytest
from zoneinfo import ZoneInfo

from tn811.exporters.csv_export import TicketView, utility_summary
from tn811.exporters.kmz_export import (
    CAUTION_ICON_URL,
    HAVERSINE_THRESHOLD_M,
    _build_and_save,
    haversine_m,
    is_problem_ticket,
    sub_color,
    urgency_bucket,
    utility_status_bucket,
)

# KML namespace on every element from simplekml output
_NS = {"kml": "http://www.opengis.net/kml/2.2"}

CT = ZoneInfo("America/Chicago")
NOW_CT = datetime(2026, 4, 24, 12, 0, 0, tzinfo=CT)
TODAY = NOW_CT.date()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_kmz(path: Path) -> ET.Element:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        kml_name = next(n for n in names if n.endswith(".kml"))
        with z.open(kml_name) as f:
            return ET.parse(f).getroot()


def _mk(
    *,
    ticket_number: str,
    excavator: str = "NORTH RIDGE CONTRACTORS",
    call_date: date | None = None,
    expire_date: date | None = None,
    is_cancelled: bool = False,
    utility_statuses: list[dict] | None = None,
    has_late_utility: bool = False,
    late_codes: list[str] | None = None,
    blocking_codes: list[str] | None = None,
    is_ready_to_dig: bool = False,
    latitude: float | None = None,
    longitude: float | None = None,
    secondary_latitude: float | None = None,
    secondary_longitude: float | None = None,
    remarks: str = "",
) -> TicketView:
    utility_statuses = utility_statuses or []
    days_until_expire = (expire_date - TODAY).days if expire_date else None
    if is_cancelled:
        status = "cancelled"
    elif expire_date is None:
        status = "unknown"
    elif expire_date < TODAY:
        status = "expired"
    elif (expire_date - TODAY).days <= 7:
        status = "expiring_soon"
    else:
        status = "active"
    return TicketView(
        ticket_number=ticket_number,
        call_date=call_date,
        legal_start_date=None,
        expire_date=expire_date,
        days_until_expire=days_until_expire,
        county="Davidson County",
        excavator_name=excavator,
        caller_name="Test Caller",
        caller_phone="(615) 555-0100",
        work_type="CONDUIT INSTL",
        address="100 MAIN ST",
        intersection="1ST AVE",
        place="",
        done_for="NORTHSTAR FIBER",
        is_ready_to_dig=is_ready_to_dig,
        has_late_utility=has_late_utility,
        late_utility_codes=late_codes or [],
        blocking_utility_codes=blocking_codes or [],
        pending_utility_codes=[],
        utility_summary=utility_summary(utility_statuses),
        is_cancelled=is_cancelled,
        status=status,
        remarks=remarks,
        latitude=latitude,
        longitude=longitude,
        secondary_latitude=secondary_latitude,
        secondary_longitude=secondary_longitude,
        utility_statuses=utility_statuses,
    )


@pytest.fixture
def sample_views() -> list[TicketView]:
    """Five tickets covering: urgent expiry, mid-term, cancelled, late utility, no coords."""
    return [
        # A — expiring in 3 days, normal coords (point)
        _mk(
            ticket_number="A",
            call_date=date(2026, 4, 18),
            expire_date=date(2026, 4, 27),
            latitude=36.10,
            longitude=-86.73,
        ),
        # B — expiring in 10 days, LINE SEGMENT coords (>50m apart — ~200m)
        _mk(
            ticket_number="B",
            call_date=date(2026, 4, 15),
            expire_date=date(2026, 5, 4),
            latitude=36.09,
            longitude=-86.74,
            secondary_latitude=36.0920,  # ~200m north
            secondary_longitude=-86.74,
        ),
        # C — cancelled, within last 14 days
        _mk(
            ticket_number="C",
            call_date=date(2026, 4, 20),
            expire_date=date(2026, 5, 5),
            is_cancelled=True,
            latitude=36.11,
            longitude=-86.72,
        ),
        # D — late utility, coords present (problem ticket → caution icon)
        _mk(
            ticket_number="D",
            call_date=date(2026, 4, 12),
            expire_date=date(2026, 5, 1),
            latitude=36.08,
            longitude=-86.75,
            utility_statuses=[
                {"code": "GFI", "name": "Northstar Fiber", "facility": "Fiber",
                 "status": "pending", "is_late": True},
            ],
            has_late_utility=True,
            late_codes=["GFI"],
            excavator="SUMMIT UNDERGROUND",
        ),
        # E — no coords, should be silently skipped
        _mk(
            ticket_number="E",
            call_date=date(2026, 4, 20),
            expire_date=date(2026, 5, 1),
        ),
    ]


# ── Structural tests ──────────────────────────────────────────────────────────


def test_kmz_is_valid_zip_and_kml(tmp_path, sample_views):
    out = tmp_path / "out.kmz"
    _build_and_save(
        views_relevant=sample_views,
        views_contractor_other=[],
        out_path=out,
        now_ct=NOW_CT,
    )
    assert out.exists()
    with zipfile.ZipFile(out) as z:
        assert any(n.endswith(".kml") for n in z.namelist())
    root = _parse_kmz(out)
    # Document + top-level folders present
    doc = root.find("kml:Document", _NS)
    assert doc is not None
    top_folders = [f.findtext("kml:name", default="", namespaces=_NS)
                   for f in doc.findall("kml:Folder", _NS)]
    assert any("by urgency" in n for n in top_folders)
    assert any("by utility status" in n for n in top_folders)
    assert any("by sub" in n for n in top_folders)
    assert any("Cancelled relevant" in n for n in top_folders)


def test_no_coords_ticket_is_skipped_silently(tmp_path, sample_views):
    out = tmp_path / "out.kmz"
    manifest = _build_and_save(
        views_relevant=sample_views,
        views_contractor_other=[],
        out_path=out,
        now_ct=NOW_CT,
    )
    # One ticket (E) had no coords — it should be counted skipped
    assert manifest.skipped_no_coords == 1

    # Ticket E must not appear anywhere in the KML
    root = _parse_kmz(out)
    names = [n.text for n in root.iter("{http://www.opengis.net/kml/2.2}name")]
    assert "E" not in names


def test_coordinate_order_is_lon_lat(tmp_path, sample_views):
    """KML spec: coords are 'longitude,latitude[,altitude]' — reverse of DB storage."""
    out = tmp_path / "out.kmz"
    _build_and_save(
        views_relevant=sample_views,
        views_contractor_other=[],
        out_path=out,
        now_ct=NOW_CT,
    )
    root = _parse_kmz(out)
    # Ticket A is at (lat=36.10, lon=-86.73). In KML coords string: "-86.73,36.10"
    for pm in root.iter("{http://www.opengis.net/kml/2.2}Placemark"):
        name = pm.findtext("kml:name", default="", namespaces=_NS)
        if name == "A":
            coord_text = pm.findtext(
                "kml:Point/kml:coordinates", default="", namespaces=_NS
            ).strip()
            lon_str, lat_str = coord_text.split(",")[:2]
            assert abs(float(lon_str) - (-86.73)) < 1e-6
            assert abs(float(lat_str) - 36.10) < 1e-6
            return
    pytest.fail("ticket A placemark not found")


def test_long_segment_renders_line_plus_midpoint(tmp_path, sample_views):
    """Ticket B's coords are ~200m apart — should produce a LineString AND a Point."""
    out = tmp_path / "out.kmz"
    _build_and_save(
        views_relevant=sample_views,
        views_contractor_other=[],
        out_path=out,
        now_ct=NOW_CT,
    )
    root = _parse_kmz(out)
    # Count LineStrings whose coords span ticket B's range. Each folder that
    # contains B gets both a LineString and a Point, so expect 3 of each
    # (urgency + utility + sub).
    line_count = 0
    point_count_b = 0
    for pm in root.iter("{http://www.opengis.net/kml/2.2}Placemark"):
        name = pm.findtext("kml:name", default="", namespaces=_NS) or ""
        line = pm.find("kml:LineString", _NS)
        if line is not None:
            coord_text = line.findtext("kml:coordinates", default="", namespaces=_NS).strip()
            # Two points in the linestring, separated by whitespace
            if "-86.74" in coord_text and "36.09" in coord_text:
                line_count += 1
        if name == "B":
            point_count_b += 1
    assert line_count >= 3  # one per axis (urgency + utility + sub)
    assert point_count_b >= 3  # midpoint pin in each axis


def test_html_escaped_in_descriptions(tmp_path):
    """User-provided text (remarks) with HTML/script must be escaped."""
    views = [
        _mk(
            ticket_number="XSS",
            call_date=date(2026, 4, 20),
            expire_date=date(2026, 5, 1),
            latitude=36.10,
            longitude=-86.73,
            remarks='<script>alert("pwn")</script>\nLine two & ampersand',
        ),
    ]
    out = tmp_path / "out.kmz"
    _build_and_save(
        views_relevant=views,
        views_contractor_other=[],
        out_path=out,
        now_ct=NOW_CT,
    )
    root = _parse_kmz(out)
    found = False
    for pm in root.iter("{http://www.opengis.net/kml/2.2}Placemark"):
        if pm.findtext("kml:name", default="", namespaces=_NS) == "XSS":
            desc = pm.findtext("kml:description", default="", namespaces=_NS) or ""
            # The literal tag must not survive; the escaped form must
            assert "<script>" not in desc
            assert "&lt;script&gt;" in desc
            # Newline became <br> after escape
            assert "<br>" in desc
            # Ampersand survives in escaped form
            assert "&amp;" in desc
            found = True
            break
    assert found, "XSS ticket description not located"


# ── Triple-rendering (per-axis placemarks) ────────────────────────────────────


def test_active_ticket_appears_in_all_three_axes(tmp_path, sample_views):
    """Ticket A (active relevant) must appear in urgency + utility + sub axes."""
    out = tmp_path / "out.kmz"
    _build_and_save(
        views_relevant=sample_views,
        views_contractor_other=[],
        out_path=out,
        now_ct=NOW_CT,
    )
    root = _parse_kmz(out)
    # Walk Placemarks counting how many are ticket A
    placemarks_a = [
        pm for pm in root.iter("{http://www.opengis.net/kml/2.2}Placemark")
        if pm.findtext("kml:name", default="", namespaces=_NS) == "A"
    ]
    assert len(placemarks_a) == 3  # urgency + utility + sub


def test_cancelled_ticket_in_cancelled_folder_only(tmp_path, sample_views):
    out = tmp_path / "out.kmz"
    _build_and_save(
        views_relevant=sample_views,
        views_contractor_other=[],
        out_path=out,
        now_ct=NOW_CT,
    )
    root = _parse_kmz(out)
    doc = root.find("kml:Document", _NS)
    # Ticket C should appear only in Cancelled folder
    cancelled_folder = next(
        f for f in doc.findall("kml:Folder", _NS)
        if "Cancelled relevant" in (f.findtext("kml:name", default="", namespaces=_NS) or "")
    )
    pms = cancelled_folder.findall(".//kml:Placemark", _NS)
    names = [pm.findtext("kml:name", default="", namespaces=_NS) for pm in pms]
    assert "C" in names
    # And should not appear in any active folder
    for top in doc.findall("kml:Folder", _NS):
        nm = top.findtext("kml:name", default="", namespaces=_NS) or ""
        if "Active relevant" in nm:
            for pm in top.findall(".//kml:Placemark", _NS):
                assert pm.findtext("kml:name", default="", namespaces=_NS) != "C"


# ── Color determinism ────────────────────────────────────────────────────────


def test_sub_color_is_deterministic():
    name = "NORTH RIDGE CONTRACTORS"
    c1 = sub_color(name)
    c2 = sub_color(name)
    assert c1 == c2


def test_sub_color_differs_between_subs():
    # Not a strict guarantee in general, but for distinct names of normal length
    # the MD5 collision space is huge — in practice every pair is different.
    samples = [
        "NORTH RIDGE CONTRACTORS",
        "SUMMIT UNDERGROUND",
        "LAKESIDE CONSTRUCTION",
        "COASTAL UNDERGROUND",
        "DTX SOLUTIONS LLC",
    ]
    colors = {sub_color(n) for n in samples}
    assert len(colors) == len(samples)


# ── Pure helpers ─────────────────────────────────────────────────────────────


class TestHaversine:
    def test_identical_points_zero(self):
        assert haversine_m(36.1, -86.7, 36.1, -86.7) == pytest.approx(0.0, abs=1e-6)

    def test_order_of_magnitude(self):
        # ~111 km per degree of latitude
        d = haversine_m(36.0, -86.7, 37.0, -86.7)
        assert 110_000 < d < 112_000

    def test_threshold_boundary(self):
        """Verify the 50m threshold cleanly separates short vs long segments."""
        # 0.0009 deg lat ≈ 100m at Nashville latitude
        long_seg = haversine_m(36.10, -86.73, 36.1009, -86.73)
        # 0.00045 deg lat ≈ 50m
        near_thresh = haversine_m(36.10, -86.73, 36.100448, -86.73)
        # 0.0001 deg lat ≈ 11m (below threshold)
        short_seg = haversine_m(36.10, -86.73, 36.1001, -86.73)
        assert long_seg > HAVERSINE_THRESHOLD_M
        assert abs(near_thresh - 50) < 5
        assert short_seg < HAVERSINE_THRESHOLD_M


class TestBuckets:
    def test_urgency_expiring_red(self):
        v = _mk(ticket_number="X", call_date=date(2026, 4, 20),
                expire_date=date(2026, 4, 26), latitude=0, longitude=0)
        label, _ = urgency_bucket(v)
        assert label == "Expiring ≤ 4 days"

    def test_urgency_no_expire(self):
        v = _mk(ticket_number="X", call_date=date(2026, 4, 20),
                expire_date=None, latitude=0, longitude=0)
        label, _ = urgency_bucket(v)
        assert label == "No expire date"

    def test_utility_status_conflict_wins_over_late(self):
        v = _mk(
            ticket_number="X",
            expire_date=date(2026, 5, 10),
            latitude=0, longitude=0,
            has_late_utility=True,
            late_codes=["GFI"],
            utility_statuses=[
                {"code": "GFI", "status": "pending", "is_late": True},
                {"code": "MWS", "status": "not_clear", "last_response": "In Conflict"},
            ],
            blocking_codes=["MWS"],
        )
        label, _ = utility_status_bucket(v)
        assert label == "Blocked — conflict/other"

    def test_utility_status_ready(self):
        v = _mk(
            ticket_number="X",
            expire_date=date(2026, 5, 10),
            latitude=0, longitude=0,
            is_ready_to_dig=True,
            utility_statuses=[{"code": "NES", "status": "clear", "is_late": False}],
        )
        label, _ = utility_status_bucket(v)
        assert label == "Ready to dig"


def test_problem_ticket_gets_caution_icon(tmp_path, sample_views):
    """Ticket D has has_late_utility=True — placemarks must use the caution icon."""
    out = tmp_path / "out.kmz"
    _build_and_save(
        views_relevant=sample_views,
        views_contractor_other=[],
        out_path=out,
        now_ct=NOW_CT,
    )
    # simplekml may shared-style the styles at Document level. Simplest reliable
    # assertion: the caution URL appears somewhere in the KML bytes iff some
    # placemark's style uses it. The only problem ticket in the fixture is D.
    with zipfile.ZipFile(out) as z:
        name = next(n for n in z.namelist() if n.endswith(".kml"))
        kml_text = z.read(name).decode("utf-8")
    assert CAUTION_ICON_URL in kml_text, "no placemark received the caution icon"


def test_caution_icon_absent_when_no_problem_tickets(tmp_path):
    """Negative control — if no problem tickets exist, the caution URL should
    not appear at all."""
    views = [
        _mk(
            ticket_number="OK1",
            call_date=date(2026, 4, 20),
            expire_date=date(2026, 5, 1),
            latitude=36.1,
            longitude=-86.73,
            is_ready_to_dig=True,
            utility_statuses=[{"code": "NES", "status": "clear", "is_late": False}],
        ),
    ]
    out = tmp_path / "out.kmz"
    _build_and_save(
        views_relevant=views,
        views_contractor_other=[],
        out_path=out,
        now_ct=NOW_CT,
    )
    with zipfile.ZipFile(out) as z:
        name = next(n for n in z.namelist() if n.endswith(".kml"))
        kml_text = z.read(name).decode("utf-8")
    assert CAUTION_ICON_URL not in kml_text


def test_is_problem_ticket():
    assert is_problem_ticket(_mk(
        ticket_number="X", has_late_utility=True, latitude=0, longitude=0,
    ))
    assert is_problem_ticket(_mk(
        ticket_number="X", blocking_codes=["MWS"], latitude=0, longitude=0,
    ))
    assert not is_problem_ticket(_mk(
        ticket_number="X", latitude=0, longitude=0,
    ))
