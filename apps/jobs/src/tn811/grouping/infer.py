"""
grouping/infer.py — Work-group inference for relevant-related tickets.

Infers probable company, work group, and crew from ticket fields using
text similarity clustering. Does NOT use a hardcoded subcontractor list.

Strategy:
1. Normalize excavator_name → probable_company (trim legal suffixes, etc.)
2. Cluster tickets by (probable_company, work_type, county) similarity
3. Assign a group_key to each cluster
4. Populate probable_work_group from the cluster key

Similarity metric: Jaccard similarity on word tokens (fast, no ML needed).
Threshold is configurable in monitoring.yaml (grouping.cluster_similarity_threshold).
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field

from tn811.config import GroupingConfig
from tn811.models import NormalizedTicket

logger = logging.getLogger(__name__)

# Legal entity suffixes to strip when normalizing company names
_STRIP_SUFFIXES = re.compile(
    r"\b(?:LLC|INC\.?|CORP\.?|CO\.?|LTD\.?|LP|LLP|INCORPORATED|COMPANY)\b",
    re.IGNORECASE,
)
_WHITESPACE = re.compile(r"\s+")


def normalize_company_name(raw: str | None) -> str | None:
    """
    Normalize a company name for grouping purposes.

    Steps:
    - Strip legal suffixes (LLC, Inc, etc.)
    - Uppercase
    - Collapse whitespace
    - Strip leading/trailing punctuation
    """
    if not raw:
        return None
    cleaned = _STRIP_SUFFIXES.sub("", raw)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip().strip(".,;:-").upper()
    return cleaned or None


def tokenize(text: str) -> set[str]:
    """Split text into lowercase word tokens, removing short/stop words."""
    stop = {"the", "and", "or", "of", "in", "at", "to", "a", "an", "for", "tn"}
    tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    return tokens - stop


def jaccard_similarity(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def group_key_for(
    probable_company: str | None,
    work_type: str | None,
    county: str,
) -> str:
    """
    Produce a stable group key for a ticket based on company + work_type + county.

    The key is a short hash of the normalized inputs, ensuring it is filesystem
    safe and stable across runs.
    """
    parts = [
        (probable_company or "unknown").upper(),
        (work_type or "").upper(),
        county.upper(),
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def infer_work_groups(
    tickets: list[NormalizedTicket],
    config: GroupingConfig,
) -> list[NormalizedTicket]:
    """
    Infer probable_company and probable_work_group for a batch of tickets.

    Mutates tickets in place and returns them.

    Algorithm:
    1. For each ticket, normalize excavator_name → probable_company.
    2. Cluster tickets: two tickets are in the same work group if their
       (company_tokens + work_type_tokens) Jaccard similarity >= threshold.
    3. Assign probable_work_group = representative company name of the cluster.

    Args:
        tickets: List of NormalizedTicket objects (relevant-related or all).
        config:  Grouping configuration.

    Returns:
        The same list with probable_company and probable_work_group populated.
    """
    threshold = config.cluster_similarity_threshold

    # Step 1: Normalize company names
    for t in tickets:
        if t.probable_company is None:
            t.probable_company = normalize_company_name(t.excavator_name)

    # Step 2: Cluster by similarity
    clusters: list[list[NormalizedTicket]] = []

    for ticket in tickets:
        ticket_tokens = _ticket_tokens(ticket, config)
        placed = False
        for cluster in clusters:
            rep = cluster[0]
            rep_tokens = _ticket_tokens(rep, config)
            sim = jaccard_similarity(ticket_tokens, rep_tokens)
            if sim >= threshold:
                cluster.append(ticket)
                placed = True
                break
        if not placed:
            clusters.append([ticket])

    # Step 3: Assign work group labels
    for cluster in clusters:
        # Use the most common company name in the cluster as the label
        company_names = [t.probable_company for t in cluster if t.probable_company]
        if company_names:
            # Most frequent
            label = max(set(company_names), key=company_names.count)
        else:
            label = "Unknown"

        for ticket in cluster:
            ticket.probable_work_group = label

    logger.info(
        "Work group inference complete",
        extra={"tickets": len(tickets), "clusters": len(clusters)},
    )
    return tickets


def _ticket_tokens(ticket: NormalizedTicket, config: GroupingConfig) -> set[str]:
    """Build a token set from the configured grouping fields."""
    parts: list[str] = []
    for field_name in config.group_fields:
        value = getattr(ticket, field_name, None)
        if value:
            parts.append(str(value))
    return tokenize(" ".join(parts))
