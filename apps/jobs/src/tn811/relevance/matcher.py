"""
relevance/matcher.py — Relevance scoring engine.

Computes:
  - relevance_score: float 0.0–1.0
  - relevance_reasons: list[str] explaining which rules fired
  - is_relevant: bool (score >= threshold or manual include)

Design:
  - Rules are pre-compiled at startup (compile_rules).
  - Manual include/exclude overrides in config take precedence over scoring.
  - Positive rule weights are summed and clamped to 1.0.
  - Negative rule weights are summed and subtracted (floor at 0.0).
  - "field: any" rules search across all text-bearing fields + raw_text.
  - Field-specific rules only search the named field.

This module is pure — no I/O, no DB, no side effects.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from tn811.config import ContractorRule, RelevanceConfig
from tn811.models import NormalizedTicket
from tn811.relevance.rules import CompiledRule, compile_rules

logger = logging.getLogger(__name__)

# Fields searched when a rule specifies field="any"
_ANY_FIELDS = [
    "excavator_name",
    "caller_name",
    "work_type",
    "location_text",
    "remarks",
    "done_for",
    "raw_text",
]


@dataclass
class RelevanceResult:
    score: float
    reasons: list[str]
    is_relevant: bool


class RelevanceMatcher:
    """
    Evaluates ticket relevance to the tracked work type.

    Usage:
        matcher = RelevanceMatcher(config.relevance)
        result = matcher.score(ticket)
        ticket.relevance_score = result.score
        ticket.relevance_reasons = result.reasons
        ticket.is_relevant = result.is_relevant
    """

    def __init__(self, config: RelevanceConfig) -> None:
        self._config = config
        self._threshold = config.score_threshold
        self._include_overrides = set(t.upper() for t in config.overrides.include)
        self._exclude_overrides = set(t.upper() for t in config.overrides.exclude)
        self._contractor_rule = config.contractor_rule
        self._pos_rules, self._neg_rules = compile_rules(config)

        logger.info(
            "RelevanceMatcher initialized",
            extra={
                "positive_rules": len(self._pos_rules),
                "negative_rules": len(self._neg_rules),
                "threshold": self._threshold,
                "include_overrides": len(self._include_overrides),
                "exclude_overrides": len(self._exclude_overrides),
            },
        )

    def score(self, ticket: NormalizedTicket) -> RelevanceResult:
        """
        Compute the relevance score for a single ticket.

        Returns:
            RelevanceResult with score, reasons, and is_relevant.
        """
        ticket_num = ticket.ticket_number.upper()

        # ── Manual overrides ─────────────────────────────────────────────────
        if ticket_num in self._exclude_overrides:
            return RelevanceResult(
                score=0.0,
                reasons=["manual_exclude_override"],
                is_relevant=False,
            )

        if ticket_num in self._include_overrides:
            return RelevanceResult(
                score=1.0,
                reasons=["manual_include_override"],
                is_relevant=True,
            )

        # ── Build field text map ─────────────────────────────────────────────
        field_texts = _build_field_texts(ticket)

        # ── Apply positive rules ─────────────────────────────────────────────
        pos_score = 0.0
        reasons: list[str] = []

        for rule in self._pos_rules:
            matched = _apply_rule(rule, field_texts)
            if matched:
                pos_score += rule.weight
                reasons.append(f"+{rule.id}(w={rule.weight:.2f})")

        pos_score = min(pos_score, 1.0)

        # ── Apply negative rules ─────────────────────────────────────────────
        neg_score = 0.0
        for rule in self._neg_rules:
            matched = _apply_rule(rule, field_texts)
            if matched:
                neg_score += abs(rule.weight)
                reasons.append(f"-{rule.id}(w={rule.weight:.2f})")

        final_score = max(0.0, min(1.0, pos_score - neg_score))

        # Compound signal: a tracked contractor doing the tracked kind of work.
        # Lifts a ticket to the threshold even when no weighted rule fired.
        if final_score < self._threshold and _matches_contractor_rule(ticket, self._contractor_rule):
            final_score = max(final_score, self._threshold)
            reasons.append("+contractor_rule_match")

        is_relevant = final_score >= self._threshold

        logger.debug(
            "Ticket scored",
            extra={
                "ticket": ticket.ticket_number,
                "score": round(final_score, 3),
                "is_relevant": is_relevant,
                "rules_fired": len(reasons),
            },
        )

        return RelevanceResult(
            score=round(final_score, 4),
            reasons=reasons,
            is_relevant=is_relevant,
        )

    def apply(self, ticket: NormalizedTicket) -> NormalizedTicket:
        """Score a ticket and mutate its relevance fields in place. Returns ticket."""
        result = self.score(ticket)
        ticket.relevance_score = result.score
        ticket.relevance_reasons = result.reasons
        ticket.is_relevant = result.is_relevant
        return ticket


# ── Private helpers ────────────────────────────────────────────────────────────

def _normalize_text(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for subcontractor matching."""
    import re
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _build_field_texts(ticket: NormalizedTicket) -> dict[str, str]:
    """Build a dict of field_name → text content for rule evaluation."""
    texts: dict[str, str] = {}
    for field_name in _ANY_FIELDS:
        value = getattr(ticket, field_name, None)
        if value:
            texts[field_name] = str(value)

    # Include utility codes (GFI etc.) as searchable text
    if ticket.utility_codes:
        texts["utility_codes"] = " ".join(ticket.utility_codes)

    # Also include utility references as concatenated text
    if ticket.utility_references:
        texts["utility_references"] = " ".join(ticket.utility_references)

    # "any" key = concatenation of all fields
    texts["any"] = " ".join(texts.values())
    return texts


def _matches_contractor_rule(ticket: NormalizedTicket, rule: ContractorRule) -> bool:
    """
    Return True if the excavator matches one of the rule's contractor keywords
    AND the work type or remarks match one of its work keywords.

    Both sides are required: a tracked contractor doing unrelated work is not a
    hit, and neither is an unknown contractor doing the right kind of work — the
    weighted rules already cover that case. Comparison uses normalized text
    (lowercase, punctuation stripped) so "north ridge" matches "NORTH RIDGE,
    LLC.". Returns False when either keyword list is empty, so a deployment that
    does not track contractors simply gets no compound signal.
    """
    if not rule.is_enabled():
        return False

    excavator_norm = _normalize_text(ticket.excavator_name or "")
    if not any(kw in excavator_norm for kw in rule.contractor_keywords):
        return False

    combined_work = _normalize_text(
        (ticket.work_type or "") + " " + (ticket.remarks or "")
    )
    return any(kw in combined_work for kw in rule.work_keywords)


def _apply_rule(rule: CompiledRule, field_texts: dict[str, str]) -> bool:
    """Return True if the rule matches any relevant field text."""
    # Determine which fields to search
    if rule.field == "any":
        targets = [field_texts.get("any", "")]
    else:
        targets = [field_texts.get(rule.field, "")]

    for text in targets:
        if not text:
            continue

        if rule.match_type == "exact":
            compare = text if rule.case_sensitive else text.lower()
            pattern = rule.pattern if rule.case_sensitive else rule.pattern.lower()
            if compare == pattern:
                return True

        elif rule.compiled is not None:
            if rule.compiled.search(text):
                return True

    return False
