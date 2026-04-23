"""
tests/portal/test_detail.py — Tests for ticket detail page parsing.

Tests parse_detail_html() against HTML fixtures. No Playwright, no network.
"""
from __future__ import annotations

import pytest

from tn811.portal.detail import DetailRecord, parse_detail_html


BASE_URL = "https://tn811.com"


class TestParseDetailHtmlActive:
    def test_ticket_number_extracted(self, detail_html_active: str):
        record = parse_detail_html(detail_html_active, "TN20240601-100001", "Davidson County", BASE_URL)
        assert record.fields.get("ticket_number") == "TN20240601-100001"

    def test_county_extracted(self, detail_html_active: str):
        record = parse_detail_html(detail_html_active, "TN20240601-100001", "Davidson County", BASE_URL)
        assert record.fields.get("county") == "Davidson County"

    def test_call_date_extracted(self, detail_html_active: str):
        record = parse_detail_html(detail_html_active, "TN20240601-100001", "Davidson County", BASE_URL)
        assert record.fields.get("call_date") == "06/01/2024"

    def test_expiration_date_extracted(self, detail_html_active: str):
        record = parse_detail_html(detail_html_active, "TN20240601-100001", "Davidson County", BASE_URL)
        assert record.fields.get("expiration_date") == "06/16/2024"

    def test_excavator_extracted(self, detail_html_active: str):
        record = parse_detail_html(detail_html_active, "TN20240601-100001", "Davidson County", BASE_URL)
        assert record.fields.get("excavator_name") == "Acme Fiber LLC"

    def test_caller_name_extracted(self, detail_html_active: str):
        record = parse_detail_html(detail_html_active, "TN20240601-100001", "Davidson County", BASE_URL)
        assert record.fields.get("caller_name") == "John Smith"

    def test_phone_extracted(self, detail_html_active: str):
        record = parse_detail_html(detail_html_active, "TN20240601-100001", "Davidson County", BASE_URL)
        assert "615" in (record.fields.get("caller_phone") or "")

    def test_work_type_extracted(self, detail_html_active: str):
        record = parse_detail_html(detail_html_active, "TN20240601-100001", "Davidson County", BASE_URL)
        wt = record.fields.get("work_type") or ""
        assert "Fiber" in wt or "fiber" in wt

    def test_location_extracted(self, detail_html_active: str):
        record = parse_detail_html(detail_html_active, "TN20240601-100001", "Davidson County", BASE_URL)
        loc = record.fields.get("location_text") or ""
        assert "Main St" in loc or "Nashville" in loc

    def test_remarks_extracted(self, detail_html_active: str):
        record = parse_detail_html(detail_html_active, "TN20240601-100001", "Davidson County", BASE_URL)
        remarks = record.fields.get("remarks") or ""
        assert "Northstar Fiber" in remarks or "fiber" in remarks.lower()

    def test_done_for_extracted(self, detail_html_active: str):
        record = parse_detail_html(detail_html_active, "TN20240601-100001", "Davidson County", BASE_URL)
        assert record.fields.get("done_for") == "NORTHSTAR FIBER"

    def test_utility_codes_extracted(self, detail_html_active: str):
        record = parse_detail_html(detail_html_active, "TN20240601-100001", "Davidson County", BASE_URL)
        assert "GFI" in record.utility_codes

    def test_not_cancelled(self, detail_html_active: str):
        record = parse_detail_html(detail_html_active, "TN20240601-100001", "Davidson County", BASE_URL)
        assert record.is_cancelled is False

    def test_raw_text_populated(self, detail_html_active: str):
        record = parse_detail_html(detail_html_active, "TN20240601-100001", "Davidson County", BASE_URL)
        assert len(record.raw_text) > 50

    def test_returns_detail_record(self, detail_html_active: str):
        record = parse_detail_html(detail_html_active, "TN20240601-100001", "Davidson County", BASE_URL)
        assert isinstance(record, DetailRecord)


class TestParseDetailHtmlCancelled:
    def test_is_cancelled_true(self, detail_html_cancelled: str):
        record = parse_detail_html(detail_html_cancelled, "TN20240604-100004", "Davidson County", BASE_URL)
        assert record.is_cancelled is True

    def test_ticket_number_preserved(self, detail_html_cancelled: str):
        record = parse_detail_html(detail_html_cancelled, "TN20240604-100004", "Davidson County", BASE_URL)
        assert record.ticket_number == "TN20240604-100004"
