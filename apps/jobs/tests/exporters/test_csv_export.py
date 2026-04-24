"""
Tests for exporters/csv_export.py.

Fixtures are built directly as `TicketView` instances (bypassing the DB) so the
writer can be exercised in isolation. One end-to-end test goes through the real
ORM path via the `in_memory_db` fixture to catch integration regressions.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from zoneinfo import ZoneInfo

from tn811.exporters.csv_export import (
    BY_SUB_COLUMNS,
    MASTER_COLUMNS,
    MASTER_FILES,
    UTILITY_DETAIL_COLUMNS,
    TicketView,
    mirror_to_desktop,
    slugify,
    utility_summary,
    write_exports,
)

CT = ZoneInfo("America/Chicago")
NOW_CT = datetime(2026, 4, 24, 12, 0, 0, tzinfo=CT)
TODAY = NOW_CT.date()  # 2026-04-24


# ── Fixture factory ───────────────────────────────────────────────────────────


def _mk(
    *,
    ticket_number: str,
    excavator: str,
    call_date: date,
    expire_date: date | None,
    is_cancelled: bool = False,
    utility_statuses: list[dict] | None = None,
    has_late_utility: bool = False,
    late_codes: list[str] | None = None,
    blocking_codes: list[str] | None = None,
    pending_codes: list[str] | None = None,
    is_ready_to_dig: bool = False,
    county: str = "Davidson County",
    remarks: str = "",
) -> TicketView:
    utility_statuses = utility_statuses or []
    days_until_expire = (expire_date - TODAY).days if expire_date else None
    # Compute status the same way the exporter does for asserts to align
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
        county=county,
        excavator_name=excavator,
        caller_name="Test Caller",
        caller_phone="(615) 555-0100",
        work_type="FIBER BURY",
        address="100 MAIN ST",
        intersection="1ST AVE",
        place="",
        done_for="GOOGLE FIBER",
        is_ready_to_dig=is_ready_to_dig,
        has_late_utility=has_late_utility,
        late_utility_codes=late_codes or [],
        blocking_utility_codes=blocking_codes or [],
        pending_utility_codes=pending_codes or [],
        utility_summary=utility_summary(utility_statuses),
        is_cancelled=is_cancelled,
        status=status,
        remarks=remarks,
        utility_statuses=utility_statuses,
    )


@pytest.fixture
def sample_views() -> list[TicketView]:
    return [
        _mk(  # T1 — active, expiring in 3 days, 2 clear utilities
            ticket_number="T1",
            excavator="BLUE OCEAN CONTRACTORS",
            call_date=date(2026, 4, 10),
            expire_date=date(2026, 4, 27),
            utility_statuses=[
                {"code": "NES", "name": "Nashville Electric", "facility": "Electric",
                 "status": "clear", "is_late": False},
                {"code": "MWS", "name": "Metro Water", "facility": "Water",
                 "status": "clear", "is_late": False},
            ],
            is_ready_to_dig=True,
        ),
        _mk(  # T2 — active, expiring in 6 days, 1 late GFI
            ticket_number="T2",
            excavator="CUI CABLE",
            call_date=date(2026, 4, 15),
            expire_date=date(2026, 4, 30),
            utility_statuses=[
                {"code": "GFI", "name": "Google Fiber", "facility": "Fiber",
                 "status": "pending", "is_late": True},
            ],
            has_late_utility=True,
            late_codes=["GFI"],
        ),
        _mk(  # T3 — active, expiring in 20 days, 1 blocked (delayed)
            ticket_number="T3",
            excavator="BLUE OCEAN CONTRACTORS",
            call_date=date(2026, 4, 4),
            expire_date=date(2026, 5, 14),
            utility_statuses=[
                {"code": "MWS", "name": "Metro Water", "facility": "Water",
                 "status": "delayed", "is_late": False},
            ],
            blocking_codes=["MWS"],
        ),
        _mk(  # T4 — active, called today, 2 pending non-late
            ticket_number="T4",
            excavator="DTOB",
            call_date=TODAY,
            expire_date=date(2026, 5, 8),
            utility_statuses=[
                {"code": "GFI", "name": "Google Fiber", "facility": "Fiber",
                 "status": "pending", "is_late": False},
                {"code": "NES", "name": "Nashville Electric", "facility": "Electric",
                 "status": "pending", "is_late": False},
            ],
            pending_codes=["GFI", "NES"],
        ),
        _mk(  # T5 — cancelled 4 days ago
            ticket_number="T5",
            excavator="CUI CABLE",
            call_date=date(2026, 4, 20),
            expire_date=date(2026, 5, 5),
            is_cancelled=True,
            utility_statuses=[],
        ),
        _mk(  # T6 — active, excavator empty, no utilities
            ticket_number="T6",
            excavator="",
            call_date=date(2026, 4, 10),
            expire_date=date(2026, 5, 20),
            utility_statuses=[],
        ),
        _mk(  # T7 — active, expiring in 2 days, 1 late + 1 blocked
            ticket_number="T7",
            excavator="BLUE OCEAN CONTRACTORS",
            call_date=date(2026, 4, 8),
            expire_date=date(2026, 4, 26),
            utility_statuses=[
                {"code": "B01", "name": "ATT", "facility": "Phone",
                 "status": "pending", "is_late": True},
                {"code": "MWS", "name": "Metro Water", "facility": "Water",
                 "status": "not_clear", "is_late": False},
            ],
            has_late_utility=True,
            late_codes=["B01"],
            blocking_codes=["MWS"],
        ),
        _mk(  # T8 — active, already expired 4 days ago
            ticket_number="T8",
            excavator="DTOB",
            call_date=date(2026, 3, 20),
            expire_date=date(2026, 4, 20),
            utility_statuses=[
                {"code": "NES", "name": "Nashville Electric", "facility": "Electric",
                 "status": "clear", "is_late": False},
            ],
            is_ready_to_dig=True,
        ),
    ]


def _read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = [dict(zip(header, r)) for r in reader]
    return header, rows


# ── Master-file filter tests ──────────────────────────────────────────────────


def test_active_csv(tmp_path, sample_views):
    write_exports(sample_views, tmp_path, sub_slices=False, now_ct=NOW_CT)
    header, rows = _read_csv(tmp_path / "gfiber_active.csv")

    assert header == list(MASTER_COLUMNS)
    assert len(rows) == 7  # all except T5 (cancelled)
    tickets = {r["ticket_number"] for r in rows}
    assert tickets == {"T1", "T2", "T3", "T4", "T6", "T7", "T8"}

    # Spot-check T1 column values
    t1 = next(r for r in rows if r["ticket_number"] == "T1")
    assert t1["call_date"] == "2026-04-10"
    assert t1["expire_date"] == "2026-04-27"
    assert t1["days_until_expire"] == "3"
    assert t1["county"] == "Davidson County"
    assert t1["is_ready_to_dig"] == "TRUE"
    assert t1["is_cancelled"] == "FALSE"
    assert t1["status"] == "expiring_soon"
    assert t1["utility_summary"] == "2 clear"


def test_expiring_4d_csv(tmp_path, sample_views):
    write_exports(sample_views, tmp_path, sub_slices=False, now_ct=NOW_CT)
    _, rows = _read_csv(tmp_path / "gfiber_expiring_4d.csv")
    # T1 (+3d), T7 (+2d), T8 (-4d — already expired qualifies since <=4)
    assert {r["ticket_number"] for r in rows} == {"T1", "T7", "T8"}
    # Verify days_until_expire rendered as signed int
    t8 = next(r for r in rows if r["ticket_number"] == "T8")
    assert t8["days_until_expire"] == "-4"
    assert t8["status"] == "expired"


def test_expiring_7d_csv(tmp_path, sample_views):
    write_exports(sample_views, tmp_path, sub_slices=False, now_ct=NOW_CT)
    _, rows = _read_csv(tmp_path / "gfiber_expiring_7d.csv")
    assert {r["ticket_number"] for r in rows} == {"T1", "T2", "T7", "T8"}


def test_late_utilities_csv(tmp_path, sample_views):
    write_exports(sample_views, tmp_path, sub_slices=False, now_ct=NOW_CT)
    _, rows = _read_csv(tmp_path / "gfiber_late_utilities.csv")
    assert {r["ticket_number"] for r in rows} == {"T2", "T7"}
    t2 = next(r for r in rows if r["ticket_number"] == "T2")
    assert t2["has_late_utility"] == "TRUE"
    assert t2["late_utility_codes"] == "GFI"
    assert t2["utility_summary"] == "1 late (GFI)"


def test_blocked_csv(tmp_path, sample_views):
    write_exports(sample_views, tmp_path, sub_slices=False, now_ct=NOW_CT)
    _, rows = _read_csv(tmp_path / "gfiber_blocked.csv")
    assert {r["ticket_number"] for r in rows} == {"T3", "T7"}
    t3 = next(r for r in rows if r["ticket_number"] == "T3")
    assert t3["blocking_utility_codes"] == "MWS"
    assert t3["utility_summary"] == "1 blocked (MWS)"


def test_new_24h_csv(tmp_path, sample_views):
    write_exports(sample_views, tmp_path, sub_slices=False, now_ct=NOW_CT)
    _, rows = _read_csv(tmp_path / "gfiber_new_24h.csv")
    # Only T4 has call_date == today
    assert [r["ticket_number"] for r in rows] == ["T4"]
    assert rows[0]["utility_summary"] == "2 pending"


def test_cancelled_7d_csv(tmp_path, sample_views):
    write_exports(sample_views, tmp_path, sub_slices=False, now_ct=NOW_CT)
    _, rows = _read_csv(tmp_path / "gfiber_cancelled_7d.csv")
    assert [r["ticket_number"] for r in rows] == ["T5"]
    assert rows[0]["is_cancelled"] == "TRUE"
    assert rows[0]["status"] == "cancelled"


def test_by_sub_csv(tmp_path, sample_views):
    write_exports(sample_views, tmp_path, sub_slices=False, now_ct=NOW_CT)
    header, rows = _read_csv(tmp_path / "gfiber_by_sub.csv")

    assert header == list(BY_SUB_COLUMNS)
    # T6 has empty excavator → excluded. Three excavators: Blue Ocean, CUI, DTOB.
    by_name = {r["excavator_name"]: r for r in rows}
    assert set(by_name) == {"BLUE OCEAN CONTRACTORS", "CUI CABLE", "DTOB"}

    blue = by_name["BLUE OCEAN CONTRACTORS"]
    assert blue["active_count"] == "3"  # T1, T3, T7
    assert blue["expiring_4d_count"] == "2"  # T1, T7
    assert blue["expiring_7d_count"] == "2"  # T1, T7 (T3 is +20d)
    assert blue["late_utilities_count"] == "1"  # T7
    assert blue["blocked_count"] == "2"  # T3 and T7 both have blocking codes
    assert blue["primary_county"] == "Davidson County"

    cui = by_name["CUI CABLE"]
    assert cui["active_count"] == "1"  # T2
    assert cui["cancelled_last_7d_count"] == "1"  # T5
    assert cui["late_utilities_count"] == "1"  # T2

    # Sort order: by active_count desc, then name asc
    assert [r["excavator_name"] for r in rows][0] == "BLUE OCEAN CONTRACTORS"


def test_utility_detail_csv(tmp_path, sample_views):
    write_exports(sample_views, tmp_path, sub_slices=False, now_ct=NOW_CT)
    header, rows = _read_csv(tmp_path / "gfiber_utility_detail.csv")

    assert header == list(UTILITY_DETAIL_COLUMNS)
    # One row per utility on each non-cancelled ticket:
    # T1:2 + T2:1 + T3:1 + T4:2 + T6:0 + T7:2 + T8:1 = 9
    assert len(rows) == 9

    # Sorted by ticket_number then utility_code
    keys = [(r["ticket_number"], r["utility_code"]) for r in rows]
    assert keys == sorted(keys)

    # Spot-check a late row
    late_row = next(r for r in rows if r["ticket_number"] == "T2")
    assert late_row["utility_code"] == "GFI"
    assert late_row["is_late"] == "TRUE"
    assert late_row["status"] == "pending"
    assert late_row["ticket_is_cancelled"] == "FALSE"

    # days_since_call for T4 (called today) is 0
    t4_rows = [r for r in rows if r["ticket_number"] == "T4"]
    assert all(r["days_since_call"] == "0" for r in t4_rows)


def test_master_file_names_match_constant(tmp_path, sample_views):
    write_exports(sample_views, tmp_path, sub_slices=False, now_ct=NOW_CT)
    for name in MASTER_FILES:
        assert (tmp_path / name).exists(), f"Missing expected master file: {name}"


# ── Sub-slice tests ───────────────────────────────────────────────────────────


def test_sub_slices_created(tmp_path, sample_views):
    manifest = write_exports(sample_views, tmp_path, sub_slices=True, now_ct=NOW_CT)
    by_sub = tmp_path / "by_sub"
    assert by_sub.is_dir()

    # Three subs with active rows; T6 (empty name) excluded
    slugs = {p.name for p in by_sub.iterdir() if p.is_dir()}
    assert slugs == {"blue-ocean-contractors", "cui-cable", "dtob"}

    # Blue Ocean: 3 active, 2 expiring_7d, 1 late_utilities
    blue = by_sub / "blue-ocean-contractors"
    _, active = _read_csv(blue / "active.csv")
    assert {r["ticket_number"] for r in active} == {"T1", "T3", "T7"}
    _, expiring = _read_csv(blue / "expiring_7d.csv")
    assert {r["ticket_number"] for r in expiring} == {"T1", "T7"}
    _, late = _read_csv(blue / "late_utilities.csv")
    assert {r["ticket_number"] for r in late} == {"T7"}

    # DTOB: 2 active (T4, T8). T8 is already-expired (days=-4) so it qualifies
    # as expiring_7d. No late utilities, so that file is skipped.
    dtob = by_sub / "dtob"
    _, dtob_active = _read_csv(dtob / "active.csv")
    assert {r["ticket_number"] for r in dtob_active} == {"T4", "T8"}
    _, dtob_expiring = _read_csv(dtob / "expiring_7d.csv")
    assert {r["ticket_number"] for r in dtob_expiring} == {"T8"}
    assert not (dtob / "late_utilities.csv").exists()

    assert manifest.sub_slice_count == 3


def test_name_variants_collapse_to_single_slug(tmp_path):
    """Two raw excavator names that slugify the same must merge into one folder/row."""
    views = [
        _mk(
            ticket_number="A1",
            excavator="Civcom Construction",
            call_date=date(2026, 4, 10),
            expire_date=date(2026, 4, 30),
        ),
        _mk(
            ticket_number="A2",
            excavator="CIVCOM CONSTRUCTION",  # casing-only variant → same slug
            call_date=date(2026, 4, 11),
            expire_date=date(2026, 5, 1),
        ),
        _mk(
            ticket_number="A3",
            excavator="  Civcom   Construction  ",  # whitespace variant → same slug
            call_date=date(2026, 4, 12),
            expire_date=date(2026, 5, 2),
        ),
    ]
    manifest = write_exports(views, tmp_path, sub_slices=True, now_ct=NOW_CT)

    # Only one by_sub folder should exist, containing all three tickets
    slug_dirs = [p.name for p in (tmp_path / "by_sub").iterdir() if p.is_dir()]
    assert slug_dirs == ["civcom-construction"]
    assert manifest.sub_slice_count == 1

    _, active = _read_csv(tmp_path / "by_sub" / "civcom-construction" / "active.csv")
    assert {r["ticket_number"] for r in active} == {"A1", "A2", "A3"}

    # by_sub CSV has a single row with active_count=3 (not three rows with count=1)
    _, sub_rows = _read_csv(tmp_path / "gfiber_by_sub.csv")
    assert len(sub_rows) == 1
    assert sub_rows[0]["active_count"] == "3"


def test_sub_slices_subtree_rebuilt_each_run(tmp_path, sample_views):
    """Stale sub folders from a previous run must be wiped before the next run."""
    # First run — seed a stale sub folder that won't appear in the second run.
    write_exports(sample_views, tmp_path, sub_slices=True, now_ct=NOW_CT)
    stale = tmp_path / "by_sub" / "ghost-sub"
    stale.mkdir(parents=True)
    (stale / "active.csv").write_text("stale\n")
    assert stale.exists()

    # Second run must remove the stale folder.
    write_exports(sample_views, tmp_path, sub_slices=True, now_ct=NOW_CT)
    assert not stale.exists()


# ── Cleanup behavior ──────────────────────────────────────────────────────────


def test_stale_master_files_removed(tmp_path, sample_views):
    stale = tmp_path / "gfiber_active.csv"
    stale.write_text("ticket_number\nOLD\n")
    assert stale.read_text().startswith("ticket_number\nOLD")

    write_exports(sample_views, tmp_path, sub_slices=False, now_ct=NOW_CT)

    _, rows = _read_csv(stale)
    assert "OLD" not in {r["ticket_number"] for r in rows}


def test_manifest_written(tmp_path, sample_views):
    write_exports(
        sample_views, tmp_path, sub_slices=True, now_ct=NOW_CT,
        db_stats={
            "total_tickets": 100,
            "total_gfiber": 8,
            "call_date_min": date(2026, 3, 20),
            "call_date_max": date(2026, 4, 24),
        },
    )
    manifest_text = (tmp_path / "MANIFEST.txt").read_text()
    assert "Generated at:" in manifest_text
    assert "100 total tickets" in manifest_text
    assert "8 GFiber" in manifest_text
    for name in MASTER_FILES:
        assert name in manifest_text
    assert "Per-subcontractor slices (3 subs)" in manifest_text


# ── Pure-function unit tests ──────────────────────────────────────────────────


class TestSlugify:
    def test_basic(self):
        assert slugify("Blue Ocean Contractors") == "blue-ocean-contractors"

    def test_special_chars(self):
        assert slugify("CUI Cable & Services, LLC") == "cui-cable-services-llc"

    def test_consecutive_spaces_and_punct(self):
        assert slugify("Foo   ---   Bar!!!") == "foo-bar"

    def test_leading_trailing_dashes(self):
        assert slugify("--Prefix Suffix--") == "prefix-suffix"

    def test_empty(self):
        assert slugify("") == "unknown"
        assert slugify("   ") == "unknown"

    def test_unicode_strips_to_nothing(self):
        # Non-ASCII collapses to dashes → falls back to 'unknown' if nothing left
        assert slugify("日本語") == "unknown"
        assert slugify("Café Foo") == "caf-foo"

    def test_numbers_preserved(self):
        assert slugify("DTO Brothers 2") == "dto-brothers-2"


class TestUtilitySummary:
    def test_empty(self):
        assert utility_summary([]) == ""

    def test_all_clear(self):
        statuses = [
            {"code": "A", "status": "clear", "is_late": False},
            {"code": "B", "status": "located", "is_late": False},
        ]
        assert utility_summary(statuses) == "2 clear"

    def test_clear_plus_pending(self):
        statuses = [
            {"code": "A", "status": "clear", "is_late": False},
            {"code": "B", "status": "clear", "is_late": False},
            {"code": "C", "status": "clear", "is_late": False},
            {"code": "D", "status": "clear", "is_late": False},
            {"code": "E", "status": "clear", "is_late": False},
            {"code": "F", "status": "pending", "is_late": False},
            {"code": "G", "status": "pending", "is_late": False},
        ]
        assert utility_summary(statuses) == "5 clear, 2 pending"

    def test_clear_plus_late_with_code(self):
        statuses = [
            {"code": "A", "status": "clear", "is_late": False},
            {"code": "B", "status": "clear", "is_late": False},
            {"code": "C", "status": "clear", "is_late": False},
            {"code": "GFI", "status": "pending", "is_late": True},
        ]
        assert utility_summary(statuses) == "3 clear, 1 late (GFI)"

    def test_clear_plus_blocked(self):
        statuses = [
            {"code": "A", "status": "clear", "is_late": False},
            {"code": "B", "status": "clear", "is_late": False},
            {"code": "MWS", "status": "not_clear", "is_late": False},
            {"code": "MCI", "status": "delayed", "is_late": False},
        ]
        assert utility_summary(statuses) == "2 clear, 2 blocked (MWS, MCI)"

    def test_late_and_blocked(self):
        statuses = [
            {"code": "B01", "status": "pending", "is_late": True},
            {"code": "MWS", "status": "not_clear", "is_late": False},
        ]
        assert utility_summary(statuses) == "1 late (B01), 1 blocked (MWS)"

    def test_unknown_status_skipped(self):
        statuses = [
            {"code": "A", "status": "clear", "is_late": False},
            {"code": "B", "status": "unknown", "is_late": False},
        ]
        assert utility_summary(statuses) == "1 clear"


# ── Desktop mirror ────────────────────────────────────────────────────────────


def test_mirror_copies_full_bundle(tmp_path, sample_views):
    """Files written to the primary dir must land in the mirror folder too."""
    primary = tmp_path / "exports"
    mirror = tmp_path / "windows-desktop" / "811dog"
    write_exports(sample_views, primary, sub_slices=True, now_ct=NOW_CT)

    assert mirror_to_desktop(primary, mirror) is True

    for name in MASTER_FILES:
        assert (mirror / name).exists(), f"Missing in mirror: {name}"
    assert (mirror / "MANIFEST.txt").exists()
    assert (mirror / "by_sub").is_dir()

    # Spot-check that the mirrored CSV matches the primary row-for-row
    _, primary_rows = _read_csv(primary / "gfiber_active.csv")
    _, mirror_rows = _read_csv(mirror / "gfiber_active.csv")
    assert primary_rows == mirror_rows


def test_export_skips_mirror_when_path_is_none(in_memory_db):
    """With `desktop_mirror_path=None` (the --no-desktop path in CLI), no mirror dir is written."""
    from datetime import datetime as _dt

    from tn811.db import get_session
    from tn811.exporters.csv_export import export
    from tn811.models import ORMTicket

    now = _dt.now(timezone.utc)
    with get_session() as session:
        session.add(
            ORMTicket(
                ticket_number="NOMIRROR001",
                county="Davidson County",
                state="TN",
                call_date="2026-04-20",
                expiration_date="2026-04-27",
                excavator_name="BLUE OCEAN",
                caller_name="T",
                caller_phone="",
                work_type="FIBER",
                location_text="X",
                intersection_text="Y",
                done_for="GOOGLE FIBER",
                utility_statuses=[],
                is_ready_to_dig=False,
                has_late_utility=False,
                late_utility_codes=[],
                pending_utility_codes=[],
                blocking_utility_codes=[],
                status="active",
                is_cancelled=False,
                relevance_score=1.0,
                is_gfiber_related=True,
                created_at=now,
                updated_at=now,
                latest_content_hash="h",
            )
        )

    primary = Path(in_memory_db.paths.exports_dir)
    mirror_candidate = primary.parent / "should-not-appear"

    with get_session() as session:
        export(session, primary, sub_slices=False, desktop_mirror_path=None)

    # Primary ran; mirror was never even looked at
    assert (primary / "gfiber_active.csv").exists()
    assert not mirror_candidate.exists()


def test_mirror_warns_and_returns_false_on_unwritable_path(tmp_path, sample_views, caplog):
    """A non-creatable mirror path must log a warning and return False, not raise."""
    primary = tmp_path / "exports"
    write_exports(sample_views, primary, sub_slices=False, now_ct=NOW_CT)

    # Point the mirror at a path whose parent is a regular file — mkdir will fail
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    unwritable = blocker / "child"

    import logging
    with caplog.at_level(logging.WARNING, logger="tn811.exporters.csv_export"):
        result = mirror_to_desktop(primary, unwritable)

    assert result is False
    assert any("Desktop mirror" in rec.message for rec in caplog.records)
    # Primary is untouched
    assert (primary / "gfiber_active.csv").exists()


def test_mirror_replaces_prior_bundle_not_unrelated_files(tmp_path, sample_views):
    """Unrelated files in the mirror dir must survive; only owned paths get wiped."""
    primary = tmp_path / "exports"
    mirror = tmp_path / "mirror"
    mirror.mkdir()

    # User-dropped sidecar files that the mirror must not touch
    (mirror / "notes.txt").write_text("my notes")
    (mirror / "ignore_me.xlsx").write_text("excel")

    write_exports(sample_views, primary, sub_slices=True, now_ct=NOW_CT)
    # Seed a stale prior bundle in the mirror
    (mirror / "gfiber_active.csv").write_text("stale,header\nstale,row\n")
    (mirror / "by_sub").mkdir()
    (mirror / "by_sub" / "ghost").mkdir()
    (mirror / "by_sub" / "ghost" / "active.csv").write_text("x")

    assert mirror_to_desktop(primary, mirror) is True

    # Unrelated files preserved
    assert (mirror / "notes.txt").exists()
    assert (mirror / "ignore_me.xlsx").exists()
    # Stale bundle wiped and replaced
    _, rows = _read_csv(mirror / "gfiber_active.csv")
    assert "stale" not in {r["ticket_number"] for r in rows}
    assert not (mirror / "by_sub" / "ghost").exists()


# ── End-to-end through the real ORM ───────────────────────────────────────────


def test_end_to_end_via_orm(in_memory_db):
    """Sanity: push a ticket through ORM and confirm export picks it up."""
    from datetime import datetime as _dt

    from tn811.db import get_session
    from tn811.exporters.csv_export import export
    from tn811.models import ORMTicket

    now = _dt.now(timezone.utc)
    with get_session() as session:
        session.add(
            ORMTicket(
                ticket_number="E2E001",
                county="Davidson County",
                state="TN",
                call_date="2026-04-20",
                expiration_date="2026-04-27",
                excavator_name="BLUE OCEAN CONTRACTORS",
                caller_name="Test",
                caller_phone="(615) 555-0100",
                work_type="FIBER",
                location_text="1 MAIN ST",
                intersection_text="1ST AVE",
                done_for="GOOGLE FIBER",
                utility_statuses=[
                    {"code": "NES", "status": "clear", "is_late": False},
                ],
                is_ready_to_dig=True,
                has_late_utility=False,
                late_utility_codes=[],
                pending_utility_codes=[],
                blocking_utility_codes=[],
                status="active",
                is_cancelled=False,
                relevance_score=1.0,
                is_gfiber_related=True,
                created_at=now,
                updated_at=now,
                latest_content_hash="deadbeef",
            )
        )

    with get_session() as session:
        manifest = export(session, Path(in_memory_db.paths.exports_dir), sub_slices=True)

    # Our one active ticket ended up in all the right places
    _, active = _read_csv(Path(in_memory_db.paths.exports_dir) / "gfiber_active.csv")
    assert [r["ticket_number"] for r in active] == ["E2E001"]
    assert manifest.total_gfiber_in_db == 1
