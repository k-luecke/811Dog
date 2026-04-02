"""
tests/pdf/test_extractor.py — Tests for PDF field extraction.

Golden tests against the sample_ticket_text fixture.
"""
from __future__ import annotations

import pytest
from datetime import date

from tn811.pdf.extractor import extract_fields, parse_date, ExtractedFields


class TestParseDate:
    def test_mm_dd_yyyy(self):
        assert parse_date("06/01/2024") == date(2024, 6, 1)

    def test_yyyy_mm_dd(self):
        assert parse_date("2024-06-01") == date(2024, 6, 1)

    def test_month_name(self):
        assert parse_date("June 1, 2024") == date(2024, 6, 1)

    def test_invalid_returns_none(self):
        assert parse_date("not a date") is None

    def test_empty_returns_none(self):
        assert parse_date("") is None

    def test_short_month_name(self):
        assert parse_date("Jan 15, 2024") == date(2024, 1, 15)


class TestExtractFields:
    def test_ticket_number_extracted(self, sample_ticket_text: str):
        result = extract_fields(sample_ticket_text)
        assert result.ticket_number == "TN20240601-100001"

    def test_county_extracted(self, sample_ticket_text: str):
        result = extract_fields(sample_ticket_text)
        assert result.county is not None
        assert "Davidson" in result.county

    def test_call_date_extracted(self, sample_ticket_text: str):
        result = extract_fields(sample_ticket_text)
        assert result.call_date == date(2024, 6, 1)

    def test_legal_start_date_extracted(self, sample_ticket_text: str):
        result = extract_fields(sample_ticket_text)
        assert result.legal_start_date == date(2024, 6, 3)

    def test_expiration_date_extracted(self, sample_ticket_text: str):
        result = extract_fields(sample_ticket_text)
        assert result.expiration_date == date(2024, 6, 16)

    def test_excavator_extracted(self, sample_ticket_text: str):
        result = extract_fields(sample_ticket_text)
        assert result.excavator_name is not None
        assert "Acme" in result.excavator_name

    def test_caller_extracted(self, sample_ticket_text: str):
        result = extract_fields(sample_ticket_text)
        assert result.caller_name is not None
        assert "Smith" in result.caller_name

    def test_work_type_extracted(self, sample_ticket_text: str):
        result = extract_fields(sample_ticket_text)
        assert result.work_type is not None
        assert "fiber" in result.work_type.lower() or "Fiber" in result.work_type

    def test_location_extracted(self, sample_ticket_text: str):
        result = extract_fields(sample_ticket_text)
        assert result.location_text is not None
        assert "Main St" in result.location_text

    def test_remarks_extracted(self, sample_ticket_text: str):
        result = extract_fields(sample_ticket_text)
        assert result.remarks is not None
        assert len(result.remarks) > 10

    def test_utilities_extracted(self, sample_ticket_text: str):
        result = extract_fields(sample_ticket_text)
        assert isinstance(result.utility_references, list)

    def test_not_cancelled(self, sample_ticket_text: str):
        result = extract_fields(sample_ticket_text)
        assert result.is_cancelled is False

    def test_cancelled_detected(self):
        text = "Ticket Number: TN20240101-999999\nStatus: CANCELLED\nVoided by request."
        result = extract_fields(text)
        assert result.is_cancelled is True

    def test_county_hint_used_when_missing(self):
        text = "Ticket Number: TN20240601-100001\nCall Date: 06/01/2024\nExpiration: 06/16/2024"
        result = extract_fields(text, county_hint="Rutherford County")
        assert result.county == "Rutherford County"

    def test_returns_extracted_fields_instance(self, sample_ticket_text: str):
        result = extract_fields(sample_ticket_text)
        assert isinstance(result, ExtractedFields)

    def test_non_fiber_ticket_no_fiber_work_type(self, sample_non_fiber_text: str):
        result = extract_fields(sample_non_fiber_text)
        if result.work_type:
            assert "fiber" not in result.work_type.lower()
