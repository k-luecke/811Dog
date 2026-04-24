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
    CIRCLE_FILL_ALPHA,
    CIRCLE_OUTLINE_ALPHA,
    CIRCLE_RADIUS_M,
    CIRCLE_VERTICES,
    HAVERSINE_THRESHOLD_M,
    _apply_alpha,
    _build_and_save,
    circle_polygon,
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
    excavator: str = "BLUE OCEAN CONTRACTORS",
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
        done_for="GOOGLE FIBER",
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
                {"code": "GFI", "name": "Google Fiber", "facility": "Fiber",
                 "status": "pending", "is_late": True},
            ],
            has_late_utility=True,
            late_codes=["GFI"],
            excavator="MAC UNDERGROUND",
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
        views_gfiber=sample_views,
        views_ervin_non_gf=[],
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
    assert any("Cancelled GFiber" in n for n in top_folders)


def test_no_coords_ticket_is_skipped_silently(tmp_path, sample_views):
    out = tmp_path / "out.kmz"
    manifest = _build_and_save(
        views_gfiber=sample_views,
        views_ervin_non_gf=[],
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
        views_gfiber=sample_views,
        views_ervin_non_gf=[],
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
        views_gfiber=sample_views,
        views_ervin_non_gf=[],
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
        views_gfiber=views,
        views_ervin_non_gf=[],
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
    """Ticket A (active GFiber) must appear in urgency + utility + sub axes."""
    out = tmp_path / "out.kmz"
    _build_and_save(
        views_gfiber=sample_views,
        views_ervin_non_gf=[],
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
        views_gfiber=sample_views,
        views_ervin_non_gf=[],
        out_path=out,
        now_ct=NOW_CT,
    )
    root = _parse_kmz(out)
    doc = root.find("kml:Document", _NS)
    # Ticket C should appear only in Cancelled folder
    cancelled_folder = next(
        f for f in doc.findall("kml:Folder", _NS)
        if "Cancelled GFiber" in (f.findtext("kml:name", default="", namespaces=_NS) or "")
    )
    pms = cancelled_folder.findall(".//kml:Placemark", _NS)
    names = [pm.findtext("kml:name", default="", namespaces=_NS) for pm in pms]
    assert "C" in names
    # And should not appear in any active folder
    for top in doc.findall("kml:Folder", _NS):
        nm = top.findtext("kml:name", default="", namespaces=_NS) or ""
        if "Active GFiber" in nm:
            for pm in top.findall(".//kml:Placemark", _NS):
                assert pm.findtext("kml:name", default="", namespaces=_NS) != "C"


# ── Color determinism ────────────────────────────────────────────────────────


def test_sub_color_is_deterministic():
    name = "BLUE OCEAN CONTRACTORS"
    c1 = sub_color(name)
    c2 = sub_color(name)
    assert c1 == c2


def test_sub_color_differs_between_subs():
    # Not a strict guarantee in general, but for distinct names of normal length
    # the MD5 collision space is huge — in practice every pair is different.
    samples = [
        "BLUE OCEAN CONTRACTORS",
        "MAC UNDERGROUND",
        "CIVCOM CONSTRUCTION",
        "FLORIDA ARMSTRONG",
        "DTOB SOLUTIONS LLC",
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
        views_gfiber=sample_views,
        views_ervin_non_gf=[],
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
        views_gfiber=views,
        views_ervin_non_gf=[],
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


# ── Dig zone circles ──────────────────────────────────────────────────────────


class TestCirclePolygon:
    def test_vertex_count_closed_ring(self):
        ring = circle_polygon(36.10, -86.73)
        # 32 vertices + 1 closing vertex = 33
        assert len(ring) == CIRCLE_VERTICES + 1
        assert ring[0] == ring[-1]

    def test_each_vertex_is_about_25m_from_center(self):
        ring = circle_polygon(36.10, -86.73)
        # Ignore the closing duplicate
        for lon, lat in ring[:-1]:
            d = haversine_m(36.10, -86.73, lat, lon)
            # Flat-earth approximation at 36° lat is tight to ~0.1 % of R
            assert abs(d - CIRCLE_RADIUS_M) < 0.5

    def test_vertex_order_is_lon_lat(self):
        """KML coordinate order: ring elements must be (lon, lat), not (lat, lon)."""
        ring = circle_polygon(36.10, -86.73)
        lon0, lat0 = ring[0]
        # Center is (36.10 lat, -86.73 lon). Ring is ~25 m out — still ~36 lat, ~-86 lon.
        assert 35.5 < lat0 < 36.5
        assert -87.5 < lon0 < -86.0

    def test_latitude_independence_of_ring_radius(self):
        """At higher latitudes, lon spacing compresses — verify the ring still reads 25 m."""
        for lat in (25.0, 36.0, 47.0):
            ring = circle_polygon(lat, -86.73)
            for lon, vlat in ring[:-1]:
                d = haversine_m(lat, -86.73, vlat, lon)
                assert abs(d - CIRCLE_RADIUS_M) < 0.5, (
                    f"radius off at lat={lat}: vertex was {d:.2f} m from center"
                )


class TestApplyAlpha:
    def test_twenty_percent_on_red(self):
        """simplekml.Color.red is 'ff0000ff'; at alpha 0.2 we expect '33' prefix."""
        assert _apply_alpha("ff0000ff", 0.20) == "330000ff"

    def test_sixty_percent_on_red(self):
        assert _apply_alpha("ff0000ff", 0.60) == "990000ff"

    def test_preserves_bgr_bytes(self):
        """Alpha change must not mangle the BGR portion."""
        # Some arbitrary AABBGGRR
        assert _apply_alpha("ff12ab34", 1.0) == "ff12ab34"
        assert _apply_alpha("ff12ab34", 0.5) == "8012ab34"


def test_circle_folder_hidden_by_default(tmp_path, sample_views):
    out = tmp_path / "out.kmz"
    _build_and_save(
        views_gfiber=sample_views,
        views_ervin_non_gf=[],
        out_path=out,
        now_ct=NOW_CT,
    )
    root = _parse_kmz(out)
    doc = root.find("kml:Document", _NS)
    dig = next(
        f for f in doc.findall("kml:Folder", _NS)
        if "Dig zone circles" in (f.findtext("kml:name", default="", namespaces=_NS) or "")
    )
    assert dig.findtext("kml:visibility", default="1", namespaces=_NS) == "0"
    # And every urgency sub-folder inside is also hidden
    for sub in dig.findall("kml:Folder", _NS):
        assert sub.findtext("kml:visibility", default="1", namespaces=_NS) == "0"


def test_polyline_ticket_gets_no_circle(tmp_path, sample_views):
    """Ticket B has primary↔secondary >50 m apart — no circle for it.
    Ticket A and D are point-rendered and should each get one circle."""
    out = tmp_path / "out.kmz"
    _build_and_save(
        views_gfiber=sample_views,
        views_ervin_non_gf=[],
        out_path=out,
        now_ct=NOW_CT,
    )
    root = _parse_kmz(out)
    doc = root.find("kml:Document", _NS)
    dig = next(
        f for f in doc.findall("kml:Folder", _NS)
        if "Dig zone circles" in (f.findtext("kml:name", default="", namespaces=_NS) or "")
    )
    polygons = dig.findall(".//kml:Polygon", _NS)
    # Sample set: A (point, active), B (polyline, active), C (cancelled),
    # D (point w/ late utility, active), E (no coords). Circles only for A + D.
    assert len(polygons) == 2

    # Also confirm ticket B's description does not appear in any dig-zone description
    dig_texts = "".join(
        (d.text or "")
        for d in dig.iter("{http://www.opengis.net/kml/2.2}description")
    )
    assert "Ticket A" in dig_texts  # A's popup rendered on its circle
    assert "Ticket D" in dig_texts
    assert "Ticket B" not in dig_texts  # polyline ticket: no circle


def test_circle_opacity_in_kml(tmp_path, sample_views):
    """Fill-alpha byte 33 (= 0x33 = 51) and outline-alpha byte 99 (= 0x99 = 153)
    must appear as the leading AA on at least one polyStyle and lineStyle in
    the output."""
    out = tmp_path / "out.kmz"
    _build_and_save(
        views_gfiber=sample_views,
        views_ervin_non_gf=[],
        out_path=out,
        now_ct=NOW_CT,
    )
    with zipfile.ZipFile(out) as z:
        name = next(n for n in z.namelist() if n.endswith(".kml"))
        kml_text = z.read(name).decode("utf-8")

    # Fill alpha 0.20 → 0x33
    assert "330000ff" in kml_text.lower(), "fill alpha 0x33 on red not found"
    # Outline alpha 0.60 → 0x99
    assert "990000ff" in kml_text.lower(), "outline alpha 0x99 on red not found"


def test_circle_count_matches_point_rendered_active_tickets(tmp_path, sample_views):
    """One circle per point-only active GFiber ticket. Polyline, cancelled,
    and non-GFiber tickets must not produce circles."""
    out = tmp_path / "out.kmz"
    _build_and_save(
        views_gfiber=sample_views,
        views_ervin_non_gf=[],
        out_path=out,
        now_ct=NOW_CT,
    )
    root = _parse_kmz(out)
    doc = root.find("kml:Document", _NS)
    dig = next(
        f for f in doc.findall("kml:Folder", _NS)
        if "Dig zone circles" in (f.findtext("kml:name", default="", namespaces=_NS) or "")
    )
    circle_count = len(dig.findall(".//kml:Polygon", _NS))
    # Count expected: active GFiber + point-rendered (not polyline)
    from tn811.exporters.kmz_export import _is_point_only
    expected = sum(
        1 for v in sample_views
        if (not v.is_cancelled) and _is_point_only(v)
    )
    assert circle_count == expected


def test_circle_ring_vertices_match_expected_geometry(tmp_path, sample_views):
    """Parse one circle's outerBoundaryIs and verify it has 33 vertices (32 closed)."""
    out = tmp_path / "out.kmz"
    _build_and_save(
        views_gfiber=sample_views,
        views_ervin_non_gf=[],
        out_path=out,
        now_ct=NOW_CT,
    )
    root = _parse_kmz(out)
    polygon = next(root.iter("{http://www.opengis.net/kml/2.2}Polygon"), None)
    assert polygon is not None
    coords_text = polygon.findtext(
        "kml:outerBoundaryIs/kml:LinearRing/kml:coordinates",
        default="",
        namespaces=_NS,
    ).strip()
    # KML coords are whitespace-separated "lon,lat[,alt]" triples/tuples
    tuples = [t for t in coords_text.split() if t]
    assert len(tuples) == CIRCLE_VERTICES + 1, (
        f"expected {CIRCLE_VERTICES + 1} vertices in closed ring, got {len(tuples)}"
    )
    assert tuples[0] == tuples[-1]  # ring is closed


# ── NashDigs package layer ────────────────────────────────────────────────────


def _fake_google_pkg(
    name: str = "BNA104a",
    *,
    status: str = "Started",
    act_start: date | None = date(2026, 1, 1),
    act_end: date | None = date(2026, 6, 1),
    project_id: int = 49701,
    description: str | None = None,
):
    from tn811.connectors.nashdigs import NashDigsPackage
    return NashDigsPackage(
        project_id=project_id,
        project_name=name,
        facility_type="Communication",
        project_type="Underground",
        description=description,
        owner="Google",
        department="Google",
        division="Google",
        status=status,
        council_district="22",
        est_start_date=act_start,
        est_end_date=act_end,
        act_start_date=act_start,
        act_end_date=act_end,
        geometry_geojson={
            "type": "MultiLineString",
            "coordinates": [[[-86.73, 36.10], [-86.73, 36.101]]],
        },
    )


def _fake_conflict_pkg(
    owner: str = "Metro Nashville",
    name: str = "Main St Water Main",
    project_id: int = 99001,
):
    from tn811.connectors.nashdigs import NashDigsPackage
    return NashDigsPackage(
        project_id=project_id,
        project_name=name,
        facility_type="Water",
        project_type="Lay & Deed",
        description=None,
        owner=owner,
        department="Metro Water Services",
        division="Development Services",
        status="Started",
        council_district="19",
        est_start_date=None,
        est_end_date=None,
        act_start_date=date(2026, 4, 1),
        act_end_date=None,
        geometry_geojson={
            "type": "Polygon",
            "coordinates": [[
                [-86.73, 36.10], [-86.725, 36.10], [-86.725, 36.105],
                [-86.73, 36.105], [-86.73, 36.10],
            ]],
        },
    )


def test_google_packages_split_into_three_subfolders(tmp_path, sample_views):
    out = tmp_path / "out.kmz"
    _build_and_save(
        views_gfiber=sample_views,
        views_ervin_non_gf=[],
        out_path=out,
        now_ct=NOW_CT,
        nashdigs_google=[
            _fake_google_pkg(name="BNA100a", status="Started", project_id=1),
            _fake_google_pkg(name="BNA100b", status="Committed", project_id=2,
                             act_start=date(2026, 5, 1), act_end=date(2026, 9, 1)),
            # Recently-completed (ActEnd within 30 days of NOW_CT 2026-04-24)
            _fake_google_pkg(name="BNA100c", status="Started", project_id=3,
                             act_start=date(2026, 1, 1), act_end=date(2026, 4, 15)),
        ],
    )
    root = _parse_kmz(out)
    doc = root.find("kml:Document", _NS)
    pkg_parent = next(
        f for f in doc.findall("kml:Folder", _NS)
        if "NashDigs Packages" in (f.findtext("kml:name", default="", namespaces=_NS) or "")
    )
    sub_names = [
        f.findtext("kml:name", default="", namespaces=_NS)
        for f in pkg_parent.findall("kml:Folder", _NS)
    ]
    assert any("Active packages" in n for n in sub_names)
    assert any("Committed" in n for n in sub_names)
    assert any("Recently completed" in n for n in sub_names)


def test_conflict_folder_only_appears_when_non_google_packages_present(tmp_path, sample_views):
    """Without non-Google packages, the conflict folder must not be created."""
    out = tmp_path / "out.kmz"
    _build_and_save(
        views_gfiber=sample_views,
        views_ervin_non_gf=[],
        out_path=out,
        now_ct=NOW_CT,
        nashdigs_google=[_fake_google_pkg()],
        nashdigs_others=[],
    )
    root = _parse_kmz(out)
    doc = root.find("kml:Document", _NS)
    names = [
        f.findtext("kml:name", default="", namespaces=_NS)
        for f in doc.findall("kml:Folder", _NS)
    ]
    assert not any("Conflict awareness" in n for n in names)


def test_conflict_folder_renders_polygon_and_line_features(tmp_path, sample_views):
    out = tmp_path / "out.kmz"
    _build_and_save(
        views_gfiber=sample_views,
        views_ervin_non_gf=[],
        out_path=out,
        now_ct=NOW_CT,
        nashdigs_google=[],
        nashdigs_others=[
            _fake_conflict_pkg(name="Main St Water Main", project_id=99001),
        ],
    )
    root = _parse_kmz(out)
    doc = root.find("kml:Document", _NS)
    conflict = next(
        f for f in doc.findall("kml:Folder", _NS)
        if "Conflict awareness" in (f.findtext("kml:name", default="", namespaces=_NS) or "")
    )
    assert conflict.findtext("kml:visibility", default="1", namespaces=_NS) == "0"
    polygons = conflict.findall(".//kml:Polygon", _NS)
    assert len(polygons) == 1


def test_package_popup_includes_ticket_counts(tmp_path, sample_views):
    out = tmp_path / "out.kmz"
    _build_and_save(
        views_gfiber=sample_views,
        views_ervin_non_gf=[],
        out_path=out,
        now_ct=NOW_CT,
        nashdigs_google=[_fake_google_pkg(name="BNA999a", project_id=42)],
        package_ticket_counts={
            "BNA999a": {"total": 5, "active": 4, "cancelled": 1,
                        "high": 3, "medium": 1, "low": 1},
        },
    )
    with zipfile.ZipFile(out) as z:
        kml_text = z.read(
            next(n for n in z.namelist() if n.endswith(".kml"))
        ).decode("utf-8")
    assert "BNA999a" in kml_text
    assert "Ervin GFiber tickets tagged" in kml_text
    # simplekml XML-escapes the HTML description, so the inner `>5<` of the
    # popup becomes `&gt;5&lt;` inside the KML text. Match that form.
    assert "&gt;5&lt;" in kml_text  # total
    assert "&gt;3&lt;" in kml_text  # high confidence


def test_header_description_mentions_circle_layer(tmp_path, sample_views):
    out = tmp_path / "out.kmz"
    _build_and_save(
        views_gfiber=sample_views,
        views_ervin_non_gf=[],
        out_path=out,
        now_ct=NOW_CT,
    )
    root = _parse_kmz(out)
    doc = root.find("kml:Document", _NS)
    desc = doc.findtext("kml:description", default="", namespaces=_NS) or ""
    assert "Dig zone circles" in desc
    assert "±25m" in desc or "25" in desc
