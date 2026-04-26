"""
interfaces.py — Plugin-style contracts for customer private data.

These protocols let customers provide project, asset, work-order, or CRM data
without coupling the public TN811 ingestion pipeline to any one vendor system.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from tn811.data_boundary import (
    ClassifiedValue,
    CustomerContext,
    DataClassification,
    DataDomain,
)
from tn811.models import NormalizedTicket


@dataclass(frozen=True)
class PrivateProject:
    """Customer-owned project record used to enrich public tickets."""

    external_id: str
    name: str
    owner: str | None = None
    status: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    classification: DataClassification = DataClassification.CUSTOMER_PRIVATE
    domain: DataDomain = DataDomain.CUSTOMER_PROJECT

    def classified_values(self, source: str) -> list[ClassifiedValue]:
        return [
            ClassifiedValue("project_external_id", self.external_id, self.classification, self.domain, source),
            ClassifiedValue("project_name", self.name, self.classification, self.domain, source),
        ]


@dataclass(frozen=True)
class EnrichmentMatch:
    """A private record matched to a public ticket with evidence."""

    provider: str
    matched_id: str
    confidence: float
    reasons: tuple[str, ...] = ()
    values: tuple[ClassifiedValue, ...] = ()


@dataclass(frozen=True)
class EnrichedTicket:
    """
    Public ticket plus optional private matches.

    The original public ticket remains unchanged. Private matches are carried
    separately so exporters can apply an explicit policy before emitting them.
    """

    public_ticket: NormalizedTicket
    customer: CustomerContext
    matches: tuple[EnrichmentMatch, ...] = ()

    def classified_values(self) -> list[ClassifiedValue]:
        values: list[ClassifiedValue] = [
            ClassifiedValue(
                "ticket_number",
                self.public_ticket.ticket_number,
                DataClassification.PUBLIC,
                DataDomain.PUBLIC_811,
                "tn811",
            ),
            ClassifiedValue(
                "county",
                self.public_ticket.county,
                DataClassification.PUBLIC,
                DataDomain.PUBLIC_811,
                "tn811",
            ),
        ]
        for match in self.matches:
            values.extend(match.values)
        return values


class PrivateProjectProvider(Protocol):
    """Adapter contract for customer project systems."""

    provider_name: str

    def list_projects(self, customer: CustomerContext) -> list[PrivateProject]:
        """Return projects visible to the supplied customer context."""


class TicketEnricher(Protocol):
    """Adapter contract for matching private data to public tickets."""

    provider_name: str

    def enrich(
        self,
        ticket: NormalizedTicket,
        customer: CustomerContext,
    ) -> EnrichedTicket:
        """Return a ticket with private matches carried separately."""

