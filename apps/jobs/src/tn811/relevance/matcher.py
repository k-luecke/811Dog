"""
relevance/matcher.py — Relevance scoring engine for GFiber ticket detection.

Computes:
  - relevance_score: float 0.0–1.0
  - relevance_reasons: list[str] explaining which rules fired
  - is_gfiber_related: bool (score >= threshold or manual include)

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

from tn811.config import RelevanceConfig
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

# Subcontractor keyword substrings (normalized lowercase, no punctuation)
# A ticket scores as GFiber-related if excavator matches one of these AND
# work_type contains a fiber/telecom keyword.  Handled by config rules but
# normalization is applied here so rules can use simple "contains" patterns.
_SUBCONTRACTOR_KEYWORDS = [
    "blue ocean",
    "florida armstrong",
    "amzung",
    "dto",
    "civcom",
    "cattail",
    "ama",
]

_FIBER_WORK_KEYWORDS = [
    "fiber", "conduit", "telecom", "boring",
]


@dataclass
class RelevanceResult:
    score: float
    reasons: list[str]
    is_gfiber_related: bool


class RelevanceMatcher:
    """
    Evaluates ticket relevance to GFiber / fiber-installation work.

    Usage:
        matcher = RelevanceMatcher(config.relevance)
        result = matcher.score(ticket)
        ticket.relevance_score = result.score
        ticket.relevance_reasons = result.reasons
        ticket.is_gfiber_related = result.is_gfiber_related
    """

    def __init__(self, config: RelevanceConfig) -> None:
        self._config = config
        self._threshold = config.score_threshold
        self._include_overrides = set(t.upper() for t in config.overrides.include)
        self._exclude_overrides = set(t.upper() for t in config.overrides.exclude)
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
            RelevanceResult with score, reasons, and is_gfiber_related.
        """
        ticket_num = ticket.ticket_number.upper()

        # ── Manual overrides ─────────────────────────────────────────────────
        if ticket_num in self._exclude_overrides:
            return RelevanceResult(
                score=0.0,
                reasons=["manual_exclude_override"],
                is_gfiber_related=False,
            )

        if ticket_num in self._include_overrides:
            return RelevanceResult(
                score=1.0,
                reasons=["manual_include_override"],
                is_gfiber_related=True,
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

        # Hard signal: subcontractor keyword match + fiber work type
        # This fires even if no other rules matched (score boost to threshold)
        if not final_score >= self._threshold and _matches_subcontractor_keywords(ticket):
            final_score = max(final_score, self._threshold)
            reasons.append("+subcontractor_keyword_match")

        is_gfiber = final_score >= self._threshold

        logger.debug(
            "Ticket scored",
            extra={
                "ticket": ticket.ticket_number,
                "score": round(final_score, 3),
                "is_gfiber": is_gfiber,
                "rules_fired": len(reasons),
            },
        )

        return RelevanceResult(
            score=round(final_score, 4),
            reasons=reasons,
            is_gfiber_related=is_gfiber,
        )

    def apply(self, ticket: NormalizedTicket) -> NormalizedTicket:
        """Score a ticket and mutate its relevance fields in place. Returns ticket."""
        result = self.score(ticket)
        ticket.relevance_score = result.score
        ticket.relevance_reasons = result.reasons
        ticket.is_gfiber_related = result.is_gfiber_related
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


def _matches_subcontractor_keywords(ticket: NormalizedTicket) -> bool:
    """
    Return True if the excavator matches a known GFiber subcontractor keyword
    AND the work type contains a fiber/telecom keyword.

    Both checks use normalized text (lowercase, no punctuation).
    """
    excavator_norm = _normalize_text(ticket.excavator_name or "")
    work_norm = _normalize_text(ticket.work_type or "")
    remarks_norm = _normalize_text(ticket.remarks or "")

    excavator_match = any(kw in excavator_norm for kw in _SUBCONTRACTOR_KEYWORDS)
    if not excavator_match:
        return False

    combined_work = work_norm + " " + remarks_norm
    return any(kw in combined_work for kw in _FIBER_WORK_KEYWORDS)


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
