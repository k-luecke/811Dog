"""
tests/test_data_boundary.py — Public/private boundary policy tests.
"""
from __future__ import annotations

import pytest

from tn811.data_boundary import (
    PUBLIC_EXPORT_POLICY,
    ClassifiedValue,
    CustomerContext,
    DataBoundaryViolation,
    DataClassification,
    DataDomain,
    ExportPolicy,
)
from tn811.integrations.interfaces import EnrichedTicket, EnrichmentMatch
from tn811.models import NormalizedTicket


def test_public_export_policy_allows_public_and_derived_values():
    values = [
        ClassifiedValue(
            "ticket_number",
            "2609013987",
            DataClassification.PUBLIC,
            DataDomain.PUBLIC_811,
            "tn811",
        ),
        ClassifiedValue(
            "relevance_score",
            0.95,
            DataClassification.DERIVED,
            DataDomain.PUBLIC_811,
            "tn811",
        ),
    ]

    PUBLIC_EXPORT_POLICY.validate(values)


def test_public_export_policy_blocks_customer_private_values():
    values = [
        ClassifiedValue(
            "project_name",
            "Internal Fiber Build",
            DataClassification.CUSTOMER_PRIVATE,
            DataDomain.CUSTOMER_PROJECT,
            "customer_csv",
        )
    ]

    with pytest.raises(DataBoundaryViolation):
        PUBLIC_EXPORT_POLICY.validate(values)


def test_customer_policy_can_opt_into_private_values():
    policy = ExportPolicy(
        name="tenant_private_export",
        allowed_classes=frozenset({
            DataClassification.PUBLIC,
            DataClassification.DERIVED,
            DataClassification.CUSTOMER_PRIVATE,
        }),
    )
    values = [
        ClassifiedValue(
            "project_name",
            "Internal Fiber Build",
            DataClassification.CUSTOMER_PRIVATE,
            DataDomain.CUSTOMER_PROJECT,
            "customer_csv",
        )
    ]

    policy.validate(values)


def test_enriched_ticket_keeps_private_values_separate_from_public_ticket():
    ticket = NormalizedTicket(ticket_number="2609013987", county="Davidson County")
    customer = CustomerContext(customer_id="cust_123")
    match = EnrichmentMatch(
        provider="customer_csv",
        matched_id="P-100",
        confidence=0.92,
        values=(
            ClassifiedValue(
                "project_name",
                "Internal Fiber Build",
                DataClassification.CUSTOMER_PRIVATE,
                DataDomain.CUSTOMER_PROJECT,
                "customer_csv",
            ),
        ),
    )

    enriched = EnrichedTicket(public_ticket=ticket, customer=customer, matches=(match,))

    assert enriched.public_ticket.ticket_number == "2609013987"
    assert enriched.matches[0].matched_id == "P-100"
    with pytest.raises(DataBoundaryViolation):
        PUBLIC_EXPORT_POLICY.validate(enriched.classified_values())
