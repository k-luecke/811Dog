"""
tests/grouping/test_grouping.py — Tests for work-group inference.
"""
from __future__ import annotations

from datetime import date

import pytest

from tn811.grouping.infer import (
    infer_work_groups,
    jaccard_similarity,
    normalize_company_name,
    tokenize,
)
from tn811.models import NormalizedTicket


def _ticket(num: str, excavator: str, work_type: str = "Fiber", county: str = "Davidson County") -> NormalizedTicket:
    return NormalizedTicket(
        ticket_number=num,
        county=county,
        excavator_name=excavator,
        work_type=work_type,
        expiration_date=date(2099, 12, 31),
    )


class TestNormalizeCompanyName:
    def test_strips_llc(self):
        assert normalize_company_name("Acme Fiber LLC") == "ACME FIBER"

    def test_strips_inc(self):
        assert normalize_company_name("Metro Inc.") == "METRO"

    def test_strips_corp(self):
        assert normalize_company_name("Big Corp") == "BIG"

    def test_uppercases(self):
        assert normalize_company_name("acme fiber") == "ACME FIBER"

    def test_none_returns_none(self):
        assert normalize_company_name(None) is None

    def test_empty_returns_none(self):
        assert normalize_company_name("") is None

    def test_collapses_whitespace(self):
        result = normalize_company_name("Acme   Fiber  Co")
        assert "  " not in result


class TestJaccardSimilarity:
    def test_identical_sets(self):
        assert jaccard_similarity({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint_sets(self):
        assert jaccard_similarity({"a", "b"}, {"c", "d"}) == 0.0

    def test_partial_overlap(self):
        sim = jaccard_similarity({"a", "b", "c"}, {"b", "c", "d"})
        assert 0.0 < sim < 1.0

    def test_empty_sets(self):
        assert jaccard_similarity(set(), set()) == 1.0


class TestInferWorkGroups:
    def test_similar_tickets_grouped(self, base_config):
        tickets = [
            _ticket("TN20240601-100001", "Acme Fiber LLC", "Fiber Install"),
            _ticket("TN20240601-100002", "Acme Fiber LLC", "Fiber Install"),
        ]
        result = infer_work_groups(tickets, base_config.grouping)
        groups = {t.probable_work_group for t in result}
        # Both should end up in the same group
        assert len(groups) == 1

    def test_different_companies_different_groups(self, base_config):
        tickets = [
            _ticket("TN20240601-100001", "Acme Fiber", "Fiber Install"),
            _ticket("TN20240601-100002", "Metro Plumbing", "Water/Sewer"),
        ]
        result = infer_work_groups(tickets, base_config.grouping)
        groups = {t.probable_work_group for t in result}
        assert len(groups) == 2

    def test_probable_company_normalized(self, base_config):
        tickets = [_ticket("TN20240601-100001", "Acme Fiber LLC")]
        result = infer_work_groups(tickets, base_config.grouping)
        assert result[0].probable_company is not None
        assert "LLC" not in result[0].probable_company

    def test_empty_list(self, base_config):
        result = infer_work_groups([], base_config.grouping)
        assert result == []

    def test_returns_same_list(self, base_config):
        tickets = [_ticket("TN20240601-100001", "Acme")]
        result = infer_work_groups(tickets, base_config.grouping)
        assert result is tickets

    def test_single_ticket_gets_group(self, base_config):
        tickets = [_ticket("TN20240601-100001", "Solo Corp")]
        result = infer_work_groups(tickets, base_config.grouping)
        assert result[0].probable_work_group is not None

    def test_no_excavator_handled_gracefully(self, base_config):
        tickets = [_ticket("TN20240601-100001", excavator="", work_type="Fiber")]
        # Should not raise
        result = infer_work_groups(tickets, base_config.grouping)
        assert len(result) == 1
