"""
tests/portal/test_search.py — Tests for portal search result parsing.

Tests parse_search_results_html() directly against HTML fixtures.
No Playwright, no network.
"""
from __future__ import annotations

import pytest

from tn811.portal.search import TicketRow, parse_search_results_html


class TestParseSearchResultsHtml:
    BASE_URL = "https://tn811.com"

    def test_parses_four_rows(self, search_results_html: str):
        rows = parse_search_results_html(search_results_html, "Davidson County", self.BASE_URL)
        assert len(rows) == 4

    def test_ticket_numbers_extracted(self, search_results_html: str):
        rows = parse_search_results_html(search_results_html, "Davidson County", self.BASE_URL)
        numbers = [r.ticket_number for r in rows]
        assert "TN20240601-100001" in numbers
        assert "TN20240602-100002" in numbers
        assert "TN20240603-100003" in numbers
        assert "TN20240604-100004" in numbers

    def test_ticket_numbers_uppercased(self, search_results_html: str):
        rows = parse_search_results_html(search_results_html, "Davidson County", self.BASE_URL)
        for row in rows:
            assert row.ticket_number == row.ticket_number.upper()

    def test_detail_urls_resolved(self, search_results_html: str):
        rows = parse_search_results_html(search_results_html, "Davidson County", self.BASE_URL)
        for row in rows:
            assert row.detail_url is not None
            assert row.detail_url.startswith("https://")

    def test_county_attached(self, search_results_html: str):
        rows = parse_search_results_html(search_results_html, "Davidson County", self.BASE_URL)
        for row in rows:
            assert row.county == "Davidson County"

    def test_excavator_names_extracted(self, search_results_html: str):
        rows = parse_search_results_html(search_results_html, "Davidson County", self.BASE_URL)
        row_map = {r.ticket_number: r for r in rows}
        assert row_map["TN20240601-100001"].excavator_name_raw == "Acme Fiber LLC"
        assert row_map["TN20240602-100002"].excavator_name_raw == "Metro Excavation Co"

    def test_work_type_extracted(self, search_results_html: str):
        rows = parse_search_results_html(search_results_html, "Davidson County", self.BASE_URL)
        row_map = {r.ticket_number: r for r in rows}
        assert "Fiber Optic Installation" in (row_map["TN20240603-100003"].work_type_raw or "")

    def test_cancelled_status_extracted(self, search_results_html: str):
        rows = parse_search_results_html(search_results_html, "Davidson County", self.BASE_URL)
        row_map = {r.ticket_number: r for r in rows}
        assert row_map["TN20240604-100004"].status_raw == "Cancelled"

    def test_empty_html_returns_empty_list(self):
        rows = parse_search_results_html("<html><body></body></html>", "Davidson County", self.BASE_URL)
        assert rows == []

    def test_no_results_table_returns_empty_list(self):
        html = "<html><body><p>No tickets found</p></body></html>"
        rows = parse_search_results_html(html, "Davidson County", self.BASE_URL)
        assert rows == []

    def test_relative_urls_resolved(self, search_results_html: str):
        rows = parse_search_results_html(search_results_html, "Davidson County", self.BASE_URL)
        for row in rows:
            if row.detail_url:
                assert "tn811.com" in row.detail_url

    def test_different_county_name_attached(self, search_results_html: str):
        rows = parse_search_results_html(search_results_html, "Rutherford County", self.BASE_URL)
        for row in rows:
            assert row.county == "Rutherford County"

    def test_returns_ticket_row_instances(self, search_results_html: str):
        rows = parse_search_results_html(search_results_html, "Davidson County", self.BASE_URL)
        for row in rows:
            assert isinstance(row, TicketRow)
