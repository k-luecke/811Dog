"""
relevance/rules.py — Rule types and loader for the relevance engine.

Rules are loaded from config (monitoring.yaml) and evaluated by matcher.py.
This module contains no matching logic — only the data structures.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from tn811.config import RelevanceConfig, RelevanceRule


@dataclass
class CompiledRule:
    """A relevance rule with its regex pre-compiled for performance."""
    id: str
    field: str          # "any" or a specific NormalizedTicket field name
    match_type: Literal["contains", "exact", "regex"]
    pattern: str
    weight: float
    case_sensitive: bool
    compiled: re.Pattern | None  # None for "exact" match_type


def compile_rules(config: RelevanceConfig) -> tuple[list[CompiledRule], list[CompiledRule]]:
    """
    Compile all positive and negative rules from config.

    Returns:
        (positive_rules, negative_rules) — lists of CompiledRule.

    Raises:
        ValueError: If a rule contains an invalid regex pattern.
    """
    pos = [_compile(r) for r in config.positive_rules]
    neg = [_compile(r) for r in config.negative_rules]
    return pos, neg


def _compile(rule: RelevanceRule) -> CompiledRule:
    flags = 0 if rule.case_sensitive else re.IGNORECASE
    compiled: re.Pattern | None = None

    if rule.match_type == "regex":
        try:
            compiled = re.compile(rule.pattern, flags)
        except re.error as exc:
            raise ValueError(
                f"Invalid regex in rule '{rule.id}': {rule.pattern!r} — {exc}"
            ) from exc
    elif rule.match_type == "contains":
        # Convert to a simple regex for uniform matching
        escaped = re.escape(rule.pattern)
        compiled = re.compile(escaped, flags)
    # "exact" uses plain equality; compiled stays None

    return CompiledRule(
        id=rule.id,
        field=rule.field,
        match_type=rule.match_type,
        pattern=rule.pattern,
        weight=rule.weight,
        case_sensitive=rule.case_sensitive,
        compiled=compiled,
    )
