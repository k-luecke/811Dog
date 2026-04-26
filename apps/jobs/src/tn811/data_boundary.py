"""
data_boundary.py — Public/private data boundary primitives.

The public TN811 pipeline must remain useful without any customer-private
systems. Private customer data can enrich public tickets only through explicit
interfaces and policies defined here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class DataClassification(StrEnum):
    """Data handling class used by models, enrichers, and exporters."""

    PUBLIC = "public"
    CUSTOMER_PRIVATE = "customer_private"
    DERIVED = "derived"
    RESTRICTED = "restricted"


class DataDomain(StrEnum):
    """High-level ownership/source domain for a record or field."""

    PUBLIC_811 = "public_811"
    PUBLIC_GIS = "public_gis"
    CUSTOMER_PROJECT = "customer_project"
    CUSTOMER_ASSET = "customer_asset"
    CUSTOMER_WORK_ORDER = "customer_work_order"
    CUSTOMER_CRM = "customer_crm"


PUBLIC_EXPORT_CLASSES = frozenset({
    DataClassification.PUBLIC,
    DataClassification.DERIVED,
})


@dataclass(frozen=True)
class ClassifiedValue:
    """A value tagged with source and handling metadata."""

    name: str
    value: Any
    classification: DataClassification
    domain: DataDomain
    source: str

    @property
    def is_public_export_safe(self) -> bool:
        return self.classification in PUBLIC_EXPORT_CLASSES


@dataclass(frozen=True)
class ExportPolicy:
    """
    Policy describing which data classes may leave a boundary.

    Public exports should use the default policy. Customer-private exports can
    opt into private classes only for tenant-scoped destinations.
    """

    name: str
    allowed_classes: frozenset[DataClassification] = PUBLIC_EXPORT_CLASSES

    def validate(self, values: list[ClassifiedValue]) -> None:
        blocked = [
            v for v in values
            if v.classification not in self.allowed_classes
        ]
        if blocked:
            names = ", ".join(f"{v.name}:{v.classification.value}" for v in blocked)
            raise DataBoundaryViolation(
                f"Export policy '{self.name}' blocks fields: {names}"
            )


PUBLIC_EXPORT_POLICY = ExportPolicy(name="public_export")


@dataclass(frozen=True)
class CustomerContext:
    """Tenant/customer scope for private enrichers and exports."""

    customer_id: str
    display_name: str | None = None
    allowed_domains: frozenset[DataDomain] = field(default_factory=frozenset)

    def allows(self, domain: DataDomain) -> bool:
        return not self.allowed_domains or domain in self.allowed_domains


class DataBoundaryViolation(ValueError):
    """Raised when private/restricted data crosses an unsafe boundary."""
