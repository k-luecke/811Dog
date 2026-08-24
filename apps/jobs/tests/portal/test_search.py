"""
tests/portal/test_search.py — Tests for portal CSV export parsing.

Tests parse_export_csv() directly against a CSV fixture.
No Playwright, no network.
"""
from __future__ import annotations

import pytest

from tn811.portal.selectors import DetailPage
from tn811.portal.search import TicketRow, parse_export_csv

BASE_URL = "https://gcv3.tn811.com"


class TestParseExportCsv:
    def test_parses_four_rows(self, search_results_csv: str):
        rows = parse_export_csv(search_results_csv, "Davidson County")
        assert len(rows) == 4

    def test_ticket_numbers_extracted(self, search_results_csv: str):
        rows = parse_export_csv(search_results_csv, "Davidson County")
        numbers = [r.ticket_number for r in rows]
        assert "2609013001" in numbers
        assert "2609013002" in numbers
        assert "2609013003" in numbers
        assert "2609013004" in numbers

    def test_ticket_numbers_uppercased(self, search_results_csv: str):
        rows = parse_export_csv(search_results_csv, "Davidson County")
        for row in rows:
            assert row.ticket_number == row.ticket_number.upper()

    def test_ticket_id_populated(self, search_results_csv: str):
        rows = parse_export_csv(search_results_csv, "Davidson County")
        for row in rows:
            assert row.ticket_id
            assert row.ticket_id.strip()

    def test_detail_urls_constructed(self, search_results_csv: str):
        rows = parse_export_csv(search_results_csv, "Davidson County")
        for row in rows:
            assert row.detail_url is not None
            assert row.ticket_id in row.detail_url
            assert "gcv3.tn811.com" in row.detail_url

    def test_detail_url_matches_template(self, search_results_csv: str):
        rows = parse_export_csv(search_results_csv, "Davidson County")
        row = rows[0]
        expected = DetailPage.URL_TEMPLATE.format(ticket_id=row.ticket_id)
        assert row.detail_url == expected

    def test_county_attached(self, search_results_csv: str):
        rows = parse_export_csv(search_results_csv, "Davidson County")
        for row in rows:
            assert row.county == "Davidson County"

    def test_excavator_names_extracted(self, search_results_csv: str):
        rows = parse_export_csv(search_results_csv, "Davidson County")
        row_map = {r.ticket_number: r for r in rows}
        assert row_map["2609013001"].excavator_name_raw == "Acme Fiber LLC"
        assert row_map["2609013002"].excavator_name_raw == "Metro Excavation Co"

    def test_work_type_extracted(self, search_results_csv: str):
        rows = parse_export_csv(search_results_csv, "Davidson County")
        row_map = {r.ticket_number: r for r in rows}
        assert "Fiber" in (row_map["2609013001"].work_type_raw or "")

    def test_work_done_for_extracted(self, search_results_csv: str):
        rows = parse_export_csv(search_results_csv, "Davidson County")
        row_map = {r.ticket_number: r for r in rows}
        assert "NORTHSTAR FIBER" == row_map["2609013001"].work_done_for_raw
        assert "GOOGLE" == row_map["2609013002"].work_done_for_raw
        assert row_map["2609013004"].work_done_for_raw is None

    def test_remarks_extracted(self, search_results_csv: str):
        rows = parse_export_csv(search_results_csv, "Davidson County")
        row_map = {r.ticket_number: r for r in rows}
        assert row_map["2609013001"].remarks_raw == "Drop bury installation"

    def test_status_type_extracted(self, search_results_csv: str):
        rows = parse_export_csv(search_results_csv, "Davidson County")
        row_map = {r.ticket_number: r for r in rows}
        assert row_map["2609013004"].status_raw == "Cancelled"
        assert row_map["2609013001"].status_raw == "Normal"

    def test_expiration_date_extracted(self, search_results_csv: str):
        rows = parse_export_csv(search_results_csv, "Davidson County")
        for row in rows:
            assert row.expiration_date_raw == "04/08/2026"

    def test_empty_csv_returns_empty_list(self):
        rows = parse_export_csv("ticketId,Number\n", "Davidson County")
        assert rows == []

    def test_header_only_returns_empty_list(self):
        header = "ticketId,Creation,WorkDate,Number,TicketType,county,WorkStreetAddress,WorkStreet,WorkIntersection,place,ExcavatorName,ExcavatorPhone,ExcavatorAddress,ExcavatorCity,ExcavatorState,ExcavatorZip,workType,Extent,Remarks,WorkDoneFor,User,ShouldUpdateBy,GoodUntil"
        rows = parse_export_csv(header + "\n", "Davidson County")
        assert rows == []

    def test_different_county_name_attached(self, search_results_csv: str):
        rows = parse_export_csv(search_results_csv, "Rutherford County")
        for row in rows:
            assert row.county == "Rutherford County"

    def test_returns_ticket_row_instances(self, search_results_csv: str):
        rows = parse_export_csv(search_results_csv, "Davidson County")
        for row in rows:
            assert isinstance(row, TicketRow)
