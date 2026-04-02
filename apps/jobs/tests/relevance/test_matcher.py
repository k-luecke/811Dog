"""
tests/relevance/test_matcher.py — Tests for the relevance scoring engine.
"""
from __future__ import annotations

import pytest
from datetime import date

from tn811.models import NormalizedTicket
from tn811.relevance.matcher import RelevanceMatcher, RelevanceResult


def _make_ticket(**kwargs) -> NormalizedTicket:
    defaults = dict(
        ticket_number="TN20240601-100001",
        county="Davidson County",
        expiration_date=date(2099, 12, 31),
    )
    defaults.update(kwargs)
    return NormalizedTicket(**defaults)


class TestRelevanceMatcher:
    def test_google_fiber_in_remarks_scores_high(self, base_config):
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(remarks="Google Fiber drop bury installation")
        result = matcher.score(ticket)
        assert result.score >= 0.9
        assert result.is_gfiber_related is True

    def test_gfiber_in_excavator_name_scores_high(self, base_config):
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(excavator_name="GFiber Install Crew")
        result = matcher.score(ticket)
        assert result.score >= 0.9

    def test_ftth_scores_well(self, base_config):
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(remarks="FTTH residential buildout")
        result = matcher.score(ticket)
        assert result.score >= 0.45
        assert result.is_gfiber_related is True

    def test_drop_bury_scores_above_threshold(self, base_config):
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(work_type="Telecom - Drop Bury")
        result = matcher.score(ticket)
        assert result.score >= 0.45

    def test_fiber_install_regex_matches(self, base_config):
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(remarks="fiber install along Main St")
        result = matcher.score(ticket)
        assert result.score > 0

    def test_pure_water_sewer_scores_low(self, base_config):
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(
            work_type="water/sewer",
            remarks="Replacing water main",
            excavator_name="Metro Plumbing",
        )
        result = matcher.score(ticket)
        assert result.is_gfiber_related is False

    def test_empty_ticket_scores_zero(self, base_config):
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket()
        result = matcher.score(ticket)
        assert result.score == 0.0
        assert result.is_gfiber_related is False

    def test_manual_include_override(self, base_config):
        base_config.relevance.overrides.include = ["TN20240601-100001"]
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(ticket_number="TN20240601-100001")
        result = matcher.score(ticket)
        assert result.score == 1.0
        assert result.is_gfiber_related is True
        assert "manual_include_override" in result.reasons

    def test_manual_exclude_override(self, base_config):
        base_config.relevance.overrides.exclude = ["TN20240601-100001"]
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(
            ticket_number="TN20240601-100001",
            remarks="Google Fiber FTTH drop bury",
        )
        result = matcher.score(ticket)
        assert result.score == 0.0
        assert result.is_gfiber_related is False
        assert "manual_exclude_override" in result.reasons

    def test_score_clamped_to_1(self, base_config):
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(
            remarks="Google Fiber FTTH fiber install drop bury underground fiber",
            excavator_name="GFiber",
            work_type="Fiber optic installation",
        )
        result = matcher.score(ticket)
        assert result.score <= 1.0

    def test_score_not_negative(self, base_config):
        base_config.relevance.negative_rules[0].weight = -5.0
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(work_type="water/sewer")
        result = matcher.score(ticket)
        assert result.score >= 0.0

    def test_apply_mutates_ticket(self, base_config):
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(remarks="Google Fiber drop bury")
        returned = matcher.apply(ticket)
        assert returned is ticket
        assert ticket.relevance_score > 0
        assert ticket.is_gfiber_related is True
        assert len(ticket.relevance_reasons) > 0

    def test_reasons_list_populated(self, base_config):
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(remarks="Google Fiber FTTH installation")
        result = matcher.score(ticket)
        assert len(result.reasons) > 0

    def test_case_insensitive_matching(self, base_config):
        matcher = RelevanceMatcher(base_config.relevance)
        for variant in ["google fiber", "GOOGLE FIBER", "Google Fiber", "gOoGlE fIbEr"]:
            ticket = _make_ticket(remarks=variant)
            result = matcher.score(ticket)
            assert result.is_gfiber_related is True, f"Failed for: {variant}"

    def test_fiber_generic_low_weight(self, base_config):
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(remarks="fiber")
        result = matcher.score(ticket)
        # Should score > 0 but below threshold
        assert result.score > 0
        assert result.score < 0.45  # fiber alone shouldn't cross threshold

    def test_returns_relevance_result(self, base_config):
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket()
        result = matcher.score(ticket)
        assert isinstance(result, RelevanceResult)

    def test_done_for_google_fiber_scores_high(self, base_config):
        """done_for field is a primary GFiber signal."""
        from tn811.config import RelevanceRule
        base_config.relevance.positive_rules.append(
            RelevanceRule(
                id="done_for_google_fiber",
                field="done_for",
                match_type="contains",
                pattern="google fiber",
                weight=1.0,
                case_sensitive=False,
            )
        )
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(done_for="GOOGLE FIBER")
        result = matcher.score(ticket)
        assert result.score >= 0.9
        assert result.is_gfiber_related is True

    def test_utility_code_gfi_scores_high(self, base_config):
        """GFI utility code is a primary GFiber signal."""
        from tn811.config import RelevanceRule
        base_config.relevance.positive_rules.append(
            RelevanceRule(
                id="utility_code_gfi",
                field="utility_codes",
                match_type="contains",
                pattern="GFI",
                weight=1.0,
                case_sensitive=False,
            )
        )
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(utility_codes=["GFI", "NES"])
        result = matcher.score(ticket)
        assert result.score >= 0.9
        assert result.is_gfiber_related is True

    def test_subcontractor_keyword_with_fiber_work_scores_above_threshold(self, base_config):
        """Subcontractor keyword + fiber work type triggers hard-signal boost."""
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(
            excavator_name="Blue Ocean Construction LLC",
            work_type="Underground Fiber Installation",
        )
        result = matcher.score(ticket)
        assert result.is_gfiber_related is True
        assert "+subcontractor_keyword_match" in result.reasons

    def test_subcontractor_keyword_without_fiber_work_does_not_score(self, base_config):
        """Subcontractor name alone (water/sewer work) should NOT trigger."""
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(
            excavator_name="Blue Ocean Construction LLC",
            work_type="Water/Sewer replacement",
        )
        result = matcher.score(ticket)
        # Should not reach threshold from subcontractor match alone since
        # work type doesn't contain fiber/telecom/conduit/boring
        assert "+subcontractor_keyword_match" not in result.reasons
