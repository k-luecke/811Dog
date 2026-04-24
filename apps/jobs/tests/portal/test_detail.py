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
        assert "Google Fiber" in remarks or "fiber" in remarks.lower()

    def test_done_for_extracted(self, detail_html_active: str):
        record = parse_detail_html(detail_html_active, "TN20240601-100001", "Davidson County", BASE_URL)
        assert record.fields.get("done_for") == "GOOGLE FIBER"

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


class TestParseDetailHtmlCoordinates:
    """Primary + secondary lat/lon live in a 4-column TD row inside the Work
    Information block. Strategy 2 in parse_detail_html already handles that
    shape; these tests confirm the new LABEL_TO_FIELD entries route the values
    into `record.fields` with the canonical field names."""

    _HTML_WITH_COORDS = """
    <html><body>
      <table>
        <tr>
          <td>Ticket Number:</td><td>2611313902</td>
          <td>County:</td><td>Davidson County</td>
        </tr>
        <tr>
          <td>Latitude:</td><td>35.891968</td>
          <td>Longitude:</td><td>-86.417549</td>
        </tr>
        <tr>
          <td>Secondary Lat:</td><td>35.892489</td>
          <td>Secondary Long:</td><td>-86.416932</td>
        </tr>
      </table>
    </body></html>
    """

    def test_primary_coords_parsed(self):
        rec = parse_detail_html(
            self._HTML_WITH_COORDS, "2611313902", "Davidson County", BASE_URL
        )
        assert rec.fields.get("latitude") == "35.891968"
        assert rec.fields.get("longitude") == "-86.417549"

    def test_secondary_coords_parsed(self):
        rec = parse_detail_html(
            self._HTML_WITH_COORDS, "2611313902", "Davidson County", BASE_URL
        )
        assert rec.fields.get("secondary_latitude") == "35.892489"
        assert rec.fields.get("secondary_longitude") == "-86.416932"

    def test_missing_coords_do_not_appear(self):
        """A detail HTML without lat/lon rows must not invent values."""
        html_no_coords = (
            "<html><body><table><tr>"
            "<td>Ticket Number:</td><td>XYZ</td>"
            "<td>County:</td><td>Davidson County</td>"
            "</tr></table></body></html>"
        )
        rec = parse_detail_html(html_no_coords, "XYZ", "Davidson County", BASE_URL)
        assert "latitude" not in rec.fields
        assert "longitude" not in rec.fields
        assert "secondary_latitude" not in rec.fields
        assert "secondary_longitude" not in rec.fields
