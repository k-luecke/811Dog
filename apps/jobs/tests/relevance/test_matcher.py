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
    def test_northstar_fiber_in_remarks_scores_high(self, base_config):
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(remarks="Northstar Fiber drop bury installation")
        result = matcher.score(ticket)
        assert result.score >= 0.9
        assert result.is_relevant is True

    def test_brand_in_excavator_name_scores_high(self, base_config):
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(excavator_name="Fiber Install Crew")
        result = matcher.score(ticket)
        assert result.score >= 0.9

    def test_ftth_scores_well(self, base_config):
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(remarks="FTTH residential buildout")
        result = matcher.score(ticket)
        assert result.score >= 0.45
        assert result.is_relevant is True

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
        assert result.is_relevant is False

    def test_empty_ticket_scores_zero(self, base_config):
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket()
        result = matcher.score(ticket)
        assert result.score == 0.0
        assert result.is_relevant is False

    def test_manual_include_override(self, base_config):
        base_config.relevance.overrides.include = ["TN20240601-100001"]
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(ticket_number="TN20240601-100001")
        result = matcher.score(ticket)
        assert result.score == 1.0
        assert result.is_relevant is True
        assert "manual_include_override" in result.reasons

    def test_manual_exclude_override(self, base_config):
        base_config.relevance.overrides.exclude = ["TN20240601-100001"]
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(
            ticket_number="TN20240601-100001",
            remarks="Northstar Fiber FTTH drop bury",
        )
        result = matcher.score(ticket)
        assert result.score == 0.0
        assert result.is_relevant is False
        assert "manual_exclude_override" in result.reasons

    def test_score_clamped_to_1(self, base_config):
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(
            remarks="Northstar Fiber FTTH fiber install drop bury underground fiber",
            excavator_name="relevant",
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

    def test_prime_contractor_done_for_scores_above_threshold(self, base_config):
        """Rows where done_for is an Meridian Cable variant must be flagged."""
        matcher = RelevanceMatcher(base_config.relevance)
        # All four Meridian-related done_for spellings seen in the real DB
        for done_for in [
            "MERIDIAN CABLE CONSTRUCTION",
            "MERIDIAN CABLE/NORTHSTAR FIBER",
            "MERIDIAN CABLE",
            "Meridian Cable Construction",  # mixed-case survives case_sensitive=False
        ]:
            ticket = _make_ticket(
                excavator_name="SUMMIT UNDERGROUND",
                work_type="CONDUIT INSTL",
                done_for=done_for,
            )
            result = matcher.score(ticket)
            assert result.is_relevant is True, (
                f"expected Meridian variant {done_for!r} to flag as relevant, "
                f"got score={result.score:.2f}"
            )
            assert any("done_for_prime_contractor" in r for r in result.reasons)

    def test_namesake_person_is_not_flagged(self, base_config):
        """A person named 'Jeff Meridian' in done_for must NOT trip the Meridian Cable rule."""
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(
            excavator_name="AIT WORLDWIDE LOGISTICS",
            work_type="GRADING",
            done_for="JEFF MERIDIAN",
        )
        result = matcher.score(ticket)
        assert result.is_relevant is False
        assert not any("done_for_prime_contractor" in r for r in result.reasons)

    def test_prime_as_excavator_only_does_not_trigger_rule(self, base_config):
        """The one ticket where Meridian is the excavator (done_for a non-fiber prime)
        must not fire the done_for-scoped Meridian rule."""
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(
            excavator_name="MERIDIAN CABLE CONSTRUCTIONS, LLC",
            work_type="HANDHOLE INSTL",
            done_for="TITANIUM LVL",
        )
        result = matcher.score(ticket)
        assert not any("done_for_prime_contractor" in r for r in result.reasons)

    def test_apply_mutates_ticket(self, base_config):
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(remarks="Northstar Fiber drop bury")
        returned = matcher.apply(ticket)
        assert returned is ticket
        assert ticket.relevance_score > 0
        assert ticket.is_relevant is True
        assert len(ticket.relevance_reasons) > 0

    def test_reasons_list_populated(self, base_config):
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(remarks="Northstar Fiber FTTH installation")
        result = matcher.score(ticket)
        assert len(result.reasons) > 0

    def test_case_insensitive_matching(self, base_config):
        matcher = RelevanceMatcher(base_config.relevance)
        for variant in ["northstar fiber", "NORTHSTAR FIBER", "Northstar Fiber", "nOrThStAr fIbEr"]:
            ticket = _make_ticket(remarks=variant)
            result = matcher.score(ticket)
            assert result.is_relevant is True, f"Failed for: {variant}"

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

    def test_done_for_target_company_scores_high(self, base_config):
        """done_for field is a primary relevance signal."""
        from tn811.config import RelevanceRule
        base_config.relevance.positive_rules.append(
            RelevanceRule(
                id="done_for_target_company",
                field="done_for",
                match_type="contains",
                pattern="northstar fiber",
                weight=1.0,
                case_sensitive=False,
            )
        )
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(done_for="NORTHSTAR FIBER")
        result = matcher.score(ticket)
        assert result.score >= 0.9
        assert result.is_relevant is True

    def test_utility_code_gfi_scores_high(self, base_config):
        """GFI utility code is a primary relevance signal."""
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
        assert result.is_relevant is True

    def test_contractor_rule_with_matching_work_scores_above_threshold(self, base_config):
        """Tracked contractor + matching work type lifts the ticket to threshold.

        Uses "Conduit placement" deliberately: no weighted rule matches it, so
        the ticket would score 0.0 and the compound contractor signal is the
        only thing that can carry it over the line. A work type the weighted
        rules already match (e.g. "Underground Fiber Installation") would score
        1.0 on its own and prove nothing about this rule.
        """
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(
            excavator_name="North Ridge Construction LLC",
            work_type="Conduit placement",
        )
        result = matcher.score(ticket)
        assert result.is_relevant is True
        assert "+contractor_rule_match" in result.reasons

    def test_contractor_rule_without_matching_work_does_not_score(self, base_config):
        """A tracked contractor doing unrelated work must NOT trigger."""
        matcher = RelevanceMatcher(base_config.relevance)
        ticket = _make_ticket(
            excavator_name="North Ridge Construction LLC",
            work_type="Water/Sewer replacement",
        )
        result = matcher.score(ticket)
        # Should not reach threshold from subcontractor match alone since
        # work type doesn't contain fiber/telecom/conduit/boring
        assert "+contractor_rule_match" not in result.reasons
