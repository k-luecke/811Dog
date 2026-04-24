"""
tests/portal/test_utility_status.py — Tests for utility status extraction and rollups.

Tests _extract_utility_statuses() against live-format HTML fixtures.
Tests _derive_util_status() for every enum mapping.
Tests _compute_utility_rollups() across all rollup fields.
No Playwright, no network.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from bs4 import BeautifulSoup

from tn811.portal.detail import _derive_util_status, _extract_utility_statuses, parse_detail_html
from tn811.normalize.tickets import _compute_utility_rollups

BASE_URL = "https://gcv3.tn811.com"


# ── _derive_util_status ───────────────────────────────────────────────────────

class TestDeriveUtilStatus:
    def test_open_no_response_is_pending(self):
        assert _derive_util_status("Open", None) == "pending"

    def test_open_locate_delayed_is_delayed(self):
        assert _derive_util_status("Open", "Locate Delayed ") == "delayed"

    def test_open_cannot_locate_is_not_clear(self):
        assert _derive_util_status("Open", "Cannot Locate - Contact Utility") == "not_clear"

    def test_open_clear_is_clear(self):
        assert _derive_util_status("Open", "Clear - No Conflict ") == "clear"

    def test_closed_clear_is_clear(self):
        assert _derive_util_status("Closed", "Clear - No Conflict ") == "clear"

    def test_closed_no_marking_needed_is_clear(self):
        assert _derive_util_status("Closed", "No Marking Needed ") == "clear"

    def test_closed_self_locate_is_clear(self):
        assert _derive_util_status("Closed", "Utility Generated Ticket - Self Locate") == "clear"

    def test_closed_located_to_meter_is_clear(self):
        assert _derive_util_status("Closed", "Located To Meter Only") == "clear"

    def test_closed_located_is_located(self):
        assert _derive_util_status("Closed", "Located - Facilities Marked ") == "located"

    def test_closed_locate_delayed_is_delayed(self):
        assert _derive_util_status("Closed", "Locate Delayed ") == "delayed"

    def test_closed_in_conflict_is_not_clear(self):
        assert _derive_util_status("Closed", "In Conflict") == "not_clear"

    def test_closed_no_response_is_not_clear(self):
        assert _derive_util_status("Closed", "No Response") == "not_clear"

    def test_unknown_combination_is_unknown(self):
        assert _derive_util_status("Closed", "Some Future Status") == "unknown"

    def test_case_insensitive_row_status(self):
        assert _derive_util_status("CLOSED", "Clear - No Conflict ") == "clear"

    def test_trailing_whitespace_in_response_normalised(self):
        # Portal includes trailing space in span text — must not cause unknown
        assert _derive_util_status("Closed", "Clear - No Conflict   ") == "clear"


# ── _extract_utility_statuses ─────────────────────────────────────────────────

class TestExtractUtilityStatusesAllClear:
    def setup_method(self, method):
        pass

    def _statuses(self, html: str):
        soup = BeautifulSoup(html, "html.parser")
        return _extract_utility_statuses(soup)

    def test_returns_four_records(self, detail_html_live_all_clear):
        statuses = self._statuses(detail_html_live_all_clear)
        assert len(statuses) == 4

    def test_gfi_code(self, detail_html_live_all_clear):
        statuses = self._statuses(detail_html_live_all_clear)
        codes = [u["code"] for u in statuses]
        assert "GFI" in codes

    def test_gfi_status_is_located(self, detail_html_live_all_clear):
        statuses = self._statuses(detail_html_live_all_clear)
        gfi = next(u for u in statuses if u["code"] == "GFI")
        assert gfi["status"] == "located"

    def test_nes_status_is_clear(self, detail_html_live_all_clear):
        statuses = self._statuses(detail_html_live_all_clear)
        nes = next(u for u in statuses if u["code"] == "NES")
        assert nes["status"] == "clear"

    def test_nyg_no_marking_needed_is_clear(self, detail_html_live_all_clear):
        statuses = self._statuses(detail_html_live_all_clear)
        nyg = next(u for u in statuses if u["code"] == "NYG")
        assert nyg["status"] == "clear"

    def test_response_timestamp_populated(self, detail_html_live_all_clear):
        statuses = self._statuses(detail_html_live_all_clear)
        gfi = next(u for u in statuses if u["code"] == "GFI")
        assert gfi["response_timestamp"] == "April 04, 2026 11:38 AM"

    def test_response_count_is_one(self, detail_html_live_all_clear):
        statuses = self._statuses(detail_html_live_all_clear)
        for u in statuses:
            assert u["response_count"] == 1

    def test_row_status_closed(self, detail_html_live_all_clear):
        statuses = self._statuses(detail_html_live_all_clear)
        for u in statuses:
            assert u["row_status"] == "Closed"

    def test_facility_populated(self, detail_html_live_all_clear):
        statuses = self._statuses(detail_html_live_all_clear)
        gfi = next(u for u in statuses if u["code"] == "GFI")
        assert gfi["facility"] == "Fiber"

    def test_nes_notes_populated(self, detail_html_live_all_clear):
        statuses = self._statuses(detail_html_live_all_clear)
        nes = next(u for u in statuses if u["code"] == "NES")
        assert nes["notes"] == "Response by Utiliquest"

    def test_is_late_not_present(self, detail_html_live_all_clear):
        # is_late is added by _compute_utility_rollups, not by extraction
        statuses = self._statuses(detail_html_live_all_clear)
        for u in statuses:
            assert "is_late" not in u


class TestExtractUtilityStatusesGfiOpen:
    def _statuses(self, html: str):
        soup = BeautifulSoup(html, "html.parser")
        return _extract_utility_statuses(soup)

    def test_returns_four_records(self, detail_html_live_gfi_open):
        assert len(self._statuses(detail_html_live_gfi_open)) == 4

    def test_gfi_is_open_pending(self, detail_html_live_gfi_open):
        statuses = self._statuses(detail_html_live_gfi_open)
        gfi = next(u for u in statuses if u["code"] == "GFI")
        assert gfi["row_status"] == "Open"
        assert gfi["status"] == "pending"
        assert gfi["last_response"] is None
        assert gfi["response_timestamp"] is None
        assert gfi["response_count"] == 0

    def test_u01_is_open_pending(self, detail_html_live_gfi_open):
        statuses = self._statuses(detail_html_live_gfi_open)
        u01 = next(u for u in statuses if u["code"] == "U01")
        assert u01["status"] == "pending"

    def test_b01_is_closed_clear(self, detail_html_live_gfi_open):
        statuses = self._statuses(detail_html_live_gfi_open)
        b01 = next(u for u in statuses if u["code"] == "B01")
        assert b01["row_status"] == "Closed"
        assert b01["status"] == "clear"


class TestExtractUtilityStatusesMixed:
    def _statuses(self, html: str):
        soup = BeautifulSoup(html, "html.parser")
        return _extract_utility_statuses(soup)

    def test_gfi_delayed(self, detail_html_live_mixed):
        statuses = self._statuses(detail_html_live_mixed)
        gfi = next(u for u in statuses if u["code"] == "GFI")
        assert gfi["status"] == "delayed"
        assert gfi["notes"] == "Crew unavailable"

    def test_att_in_conflict_is_not_clear(self, detail_html_live_mixed):
        statuses = self._statuses(detail_html_live_mixed)
        att = next(u for u in statuses if u["code"] == "ATT")
        assert att["status"] == "not_clear"

    def test_mws_open_no_response_is_pending(self, detail_html_live_mixed):
        statuses = self._statuses(detail_html_live_mixed)
        mws = next(u for u in statuses if u["code"] == "MWS")
        assert mws["status"] == "pending"
        assert mws["response_count"] == 0

    def test_nes_located(self, detail_html_live_mixed):
        statuses = self._statuses(detail_html_live_mixed)
        nes = next(u for u in statuses if u["code"] == "NES")
        assert nes["status"] == "located"


# ── parse_detail_html: Work Information field extraction ──────────────────────

class TestParseDetailHtmlLiveWorkInfo:
    def _record(self, html: str):
        return parse_detail_html(html, "2609211062", "Davidson County", BASE_URL)

    def test_address_extracted_as_location_text(self, detail_html_live_all_clear):
        record = self._record(detail_html_live_all_clear)
        assert record.fields.get("location_text") == "1109 N 6TH ST"

    def test_intersection_extracted(self, detail_html_live_all_clear):
        record = self._record(detail_html_live_all_clear)
        assert record.fields.get("intersection_text") == "EVANSTON AVE"

    def test_done_for_extracted(self, detail_html_live_all_clear):
        record = self._record(detail_html_live_all_clear)
        assert record.fields.get("done_for") == "GOOGLE FIBER 6459663"

    def test_work_type_extracted(self, detail_html_live_all_clear):
        record = self._record(detail_html_live_all_clear)
        assert record.fields.get("work_type") == "FIBER OPTIC BURYING"

    def test_legal_start_date_extracted(self, detail_html_live_all_clear):
        record = self._record(detail_html_live_all_clear)
        assert record.fields.get("legal_start_date") == "04/08/26 AT 9:30 AM"

    def test_expiration_date_extracted(self, detail_html_live_all_clear):
        record = self._record(detail_html_live_all_clear)
        assert record.fields.get("expiration_date") == "04/23/26"

    def test_utility_codes_from_notified_table(self, detail_html_live_all_clear):
        record = self._record(detail_html_live_all_clear)
        assert "GFI" in record.utility_codes
        assert "NES" in record.utility_codes

    def test_utility_statuses_populated(self, detail_html_live_all_clear):
        record = self._record(detail_html_live_all_clear)
        assert len(record.utility_statuses) == 4


# ── _compute_utility_rollups ──────────────────────────────────────────────────

def _make_status(code, status):
    return {"code": code, "name": code, "facility": "Fiber",
            "row_status": "Closed", "last_response": None,
            "response_timestamp": None, "response_count": 1,
            "notes": "", "status": status}


class TestComputeUtilityRollups:
    _now = datetime(2026, 4, 23, 18, 0, 0, tzinfo=timezone.utc)
    _recent_call = date(2026, 4, 23)    # same day — within 72h
    _old_call = date(2026, 4, 19)       # 4 days ago — outside 72h

    def test_all_clear_is_ready_to_dig(self):
        statuses = [_make_status("GFI", "located"), _make_status("NES", "clear")]
        _, rollups = _compute_utility_rollups(statuses, self._recent_call, False, self._now)
        assert rollups["is_ready_to_dig"] is True

    def test_pending_utility_not_ready(self):
        statuses = [_make_status("GFI", "located"), _make_status("MWS", "pending")]
        _, rollups = _compute_utility_rollups(statuses, self._recent_call, False, self._now)
        assert rollups["is_ready_to_dig"] is False

    def test_delayed_utility_not_ready(self):
        statuses = [_make_status("GFI", "delayed")]
        _, rollups = _compute_utility_rollups(statuses, self._recent_call, False, self._now)
        assert rollups["is_ready_to_dig"] is False

    def test_not_clear_utility_not_ready(self):
        statuses = [_make_status("ATT", "not_clear")]
        _, rollups = _compute_utility_rollups(statuses, self._recent_call, False, self._now)
        assert rollups["is_ready_to_dig"] is False

    def test_empty_statuses_not_ready(self):
        _, rollups = _compute_utility_rollups([], self._recent_call, False, self._now)
        assert rollups["is_ready_to_dig"] is False

    def test_pending_within_72h_not_late(self):
        statuses = [_make_status("GFI", "pending")]
        enriched, rollups = _compute_utility_rollups(statuses, self._recent_call, False, self._now)
        assert rollups["has_late_utility"] is False
        assert enriched[0]["is_late"] is False
        assert "GFI" in rollups["pending_utility_codes"]
        assert "GFI" not in rollups["late_utility_codes"]

    def test_pending_older_than_72h_is_late(self):
        statuses = [_make_status("GFI", "pending")]
        enriched, rollups = _compute_utility_rollups(statuses, self._old_call, False, self._now)
        assert rollups["has_late_utility"] is True
        assert enriched[0]["is_late"] is True
        assert "GFI" in rollups["late_utility_codes"]
        assert "GFI" not in rollups["pending_utility_codes"]

    def test_cancelled_ticket_no_late(self):
        statuses = [_make_status("GFI", "pending")]
        _, rollups = _compute_utility_rollups(statuses, self._old_call, True, self._now)
        assert rollups["has_late_utility"] is False

    def test_delayed_in_blocking_codes(self):
        statuses = [_make_status("GFI", "delayed")]
        _, rollups = _compute_utility_rollups(statuses, self._recent_call, False, self._now)
        assert "GFI" in rollups["blocking_utility_codes"]

    def test_not_clear_in_blocking_codes(self):
        statuses = [_make_status("ATT", "not_clear")]
        _, rollups = _compute_utility_rollups(statuses, self._recent_call, False, self._now)
        assert "ATT" in rollups["blocking_utility_codes"]

    def test_pending_not_in_blocking_codes(self):
        statuses = [_make_status("MWS", "pending")]
        _, rollups = _compute_utility_rollups(statuses, self._recent_call, False, self._now)
        assert "MWS" not in rollups["blocking_utility_codes"]

    def test_multiple_pending_all_recorded(self):
        statuses = [_make_status("GFI", "pending"), _make_status("MWS", "pending")]
        _, rollups = _compute_utility_rollups(statuses, self._recent_call, False, self._now)
        assert set(rollups["pending_utility_codes"]) == {"GFI", "MWS"}

    def test_is_late_added_to_enriched_records(self):
        statuses = [_make_status("GFI", "pending")]
        enriched, _ = _compute_utility_rollups(statuses, self._recent_call, False, self._now)
        assert "is_late" in enriched[0]

    def test_no_call_date_pending_not_late(self):
        statuses = [_make_status("GFI", "pending")]
        _, rollups = _compute_utility_rollups(statuses, None, False, self._now)
        assert rollups["has_late_utility"] is False


# ── Date parsing: 2-digit year format ─────────────────────────────────────────

class TestTwoDigitYearParsing:
    def test_legal_start_date_2dig_year_parsed(self, detail_html_live_all_clear):
        from tn811.pdf.extractor import parse_date
        raw = "04/08/26 AT 9:30 AM"
        d = parse_date(raw)
        assert d is not None
        assert d.year == 2026
        assert d.month == 4
        assert d.day == 8

    def test_expire_date_2dig_year(self):
        from tn811.pdf.extractor import parse_date
        d = parse_date("04/23/26")
        assert d is not None
        assert d.year == 2026
