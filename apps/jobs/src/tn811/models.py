"""
models.py — Pydantic schemas and SQLAlchemy ORM models for TN811 Monitor.

Design principles:
- Pydantic schemas are used for validation, normalization, and serialization.
- SQLAlchemy models are the persistence layer; they shadow the Pydantic shapes.
- The schema does NOT center on subcontractors; instead it uses:
    probable_company, probable_work_group, probable_crew
  which are inferred, not scraped.
- ticket_snapshots stores the full structured payload for each version
  (keyed on content hash), enabling audit trails without redundancy.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


# ── Enumerations ──────────────────────────────────────────────────────────────

class TicketStatus(str, Enum):
    ACTIVE = "active"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class ScrapeRunStatus(str, Enum):
    STARTED = "started"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class ParseFailureReason(str, Enum):
    PDF_DOWNLOAD_FAILED = "pdf_download_failed"
    PDF_PARSE_ERROR = "pdf_parse_error"
    MISSING_REQUIRED_FIELDS = "missing_required_fields"
    HTML_PARSE_ERROR = "html_parse_error"
    UNKNOWN = "unknown"


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class UtilityResponse(BaseModel):
    """A single utility's response to a locate request."""
    utility_name: str
    response_code: str | None = None
    response_text: str | None = None


# ── Utility status enum values ────────────────────────────────────────────────
# clear     — no conflict; no facilities in dig area
# located   — facilities exist and are marked; dig carefully
# pending   — utility has not yet responded
# delayed   — locate was delayed (may still need follow-up)
# not_clear — in conflict, cannot locate, or no response on a closed ticket
# unknown   — unrecognized portal response combination
UTIL_STATUS_CLEAR = "clear"
UTIL_STATUS_LOCATED = "located"
UTIL_STATUS_PENDING = "pending"
UTIL_STATUS_DELAYED = "delayed"
UTIL_STATUS_NOT_CLEAR = "not_clear"
UTIL_STATUS_UNKNOWN = "unknown"
_UTIL_STATUSES_READY = frozenset({UTIL_STATUS_CLEAR, UTIL_STATUS_LOCATED})


class NormalizedTicket(BaseModel):
    """
    Canonical normalized representation of a TN811 ticket.

    This is the single source of truth after PDF parsing + normalization.
    Every field is either directly parsed or explicitly None.
    """
    # Identity
    ticket_number: str = Field(..., description="TN811 ticket number, e.g. TN20240101-123456")
    county: str
    state: str = "TN"

    # Dates
    call_date: date | None = None
    legal_start_date: date | None = None
    expiration_date: date | None = None

    # Parties
    excavator_name: str | None = None
    caller_name: str | None = None
    caller_phone: str | None = None

    # Work details
    work_type: str | None = None
    location_text: str | None = None
    intersection_text: str | None = None
    remarks: str | None = None
    done_for: str | None = None          # entity the work is being done for (e.g. "NORTHSTAR FIBER")

    # Geographic coordinates (two points = line segment of the dig path).
    latitude: float | None = None
    longitude: float | None = None
    secondary_latitude: float | None = None
    secondary_longitude: float | None = None

    # Utilities
    utility_references: list[str] = Field(default_factory=list)
    utility_responses: list[UtilityResponse] = Field(default_factory=list)
    utility_codes: list[str] = Field(default_factory=list)   # short codes like ["GFI", "NES"]

    # Per-utility status records (one dict per utility row in the Response Status table).
    # Each record: {code, name, facility, row_status, last_response, response_timestamp,
    #               response_count, notes, status, is_late}
    utility_statuses: list[dict] = Field(default_factory=list)

    # Ticket-level utility rollups (derived from utility_statuses + call_date)
    is_ready_to_dig: bool = False        # True when all utilities are clear or located
    has_late_utility: bool = False       # True when any utility is pending >72h
    late_utility_codes: list[str] = Field(default_factory=list)      # pending >72h
    pending_utility_codes: list[str] = Field(default_factory=list)   # pending, within 72h
    blocking_utility_codes: list[str] = Field(default_factory=list)  # delayed or not_clear

    # Lifecycle
    status: TicketStatus = TicketStatus.UNKNOWN
    is_cancelled: bool = False

    # Relevance (populated by relevance engine)
    relevance_score: float = 0.0
    relevance_reasons: list[str] = Field(default_factory=list)
    is_relevant: bool = False

    # Inferred grouping (populated by grouping engine)
    probable_company: str | None = None
    probable_work_group: str | None = None
    probable_crew: str | None = None

    # Source metadata
    source_pdf_path: str | None = None
    source_pdf_sha256: str | None = None
    source_html_path: str | None = None
    raw_text: str | None = None

    # Audit
    parsed_at: datetime | None = None
    parse_method: str | None = None  # pdfplumber | pymupdf | ocr | html

    @field_validator("ticket_number")
    @classmethod
    def normalize_ticket_number(cls, v: str) -> str:
        return v.strip().upper()

    @model_validator(mode="after")
    def derive_status(self) -> "NormalizedTicket":
        """Derive status from cancellation flag and expiration date."""
        if self.is_cancelled:
            self.status = TicketStatus.CANCELLED
        elif self.expiration_date is not None:
            today = datetime.utcnow().date()
            self.status = TicketStatus.EXPIRED if self.expiration_date < today else TicketStatus.ACTIVE
        else:
            self.status = TicketStatus.UNKNOWN
        return self

    def content_hash(self) -> str:
        """
        SHA-256 of the canonical JSON payload, used to detect changes.

        Fields that do NOT affect the content hash (runtime metadata):
        - parsed_at, parse_method, probable_company, probable_work_group,
          probable_crew, relevance_score, relevance_reasons, is_relevant
        """
        stable_fields = self.model_dump(
            exclude={
                "parsed_at", "parse_method",
                "probable_company", "probable_work_group", "probable_crew",
                "relevance_score", "relevance_reasons", "is_relevant",
            }
        )
        payload = json.dumps(stable_fields, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()


class TicketSummary(BaseModel):
    """Lightweight summary for list views."""
    ticket_number: str
    county: str
    call_date: date | None
    expiration_date: date | None
    excavator_name: str | None
    work_type: str | None
    location_text: str | None
    status: TicketStatus
    is_relevant: bool
    relevance_score: float
    probable_company: str | None
    probable_work_group: str | None


class ParseFailureRecord(BaseModel):
    """Record of a parsing failure for the review dashboard."""
    ticket_number: str | None
    county: str
    reason: ParseFailureReason
    detail: str
    raw_url: str | None = None
    raw_path: str | None = None
    occurred_at: datetime


class ReminderPreview(BaseModel):
    """Email reminder preview (written to disk in dry-run mode)."""
    subject: str
    recipients: list[str]
    ticket_summaries: list[TicketSummary]
    generated_at: datetime
    dry_run: bool = True


# ── SQLAlchemy ORM ────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class ORMTicket(Base):
    """
    Primary ticket record — one row per unique ticket number.

    Updated in place when ticket data changes; history is preserved
    in ORMTicketSnapshot.
    """
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_number = Column(String(64), nullable=False, unique=True, index=True)
    county = Column(String(64), nullable=False, index=True)
    state = Column(String(8), nullable=False, default="TN")

    call_date = Column(String(16))          # ISO date string
    legal_start_date = Column(String(16))
    expiration_date = Column(String(16), index=True)

    excavator_name = Column(Text)
    caller_name = Column(Text)
    caller_phone = Column(String(32))

    work_type = Column(Text, index=True)
    location_text = Column(Text)
    intersection_text = Column(Text)
    remarks = Column(Text)
    done_for = Column(Text, index=True)     # entity the work is done for (e.g. NORTHSTAR FIBER)

    # Geographic coordinates (from detail page Work Information block)
    latitude = Column(Float)
    longitude = Column(Float)
    secondary_latitude = Column(Float)
    secondary_longitude = Column(Float)

    utility_references = Column(JSON)       # list[str]
    utility_responses = Column(JSON)        # list[dict]
    utility_codes = Column(JSON)            # list[str] — short codes like ["GFI", "NES"]
    utility_statuses = Column(JSON)         # list[dict] — per-utility status records
    is_ready_to_dig = Column(Boolean, default=False)
    has_late_utility = Column(Boolean, default=False)
    late_utility_codes = Column(JSON)       # list[str]
    pending_utility_codes = Column(JSON)    # list[str]
    blocking_utility_codes = Column(JSON)   # list[str]

    status = Column(String(16), nullable=False, default="unknown", index=True)
    is_cancelled = Column(Boolean, nullable=False, default=False)

    relevance_score = Column(Float, nullable=False, default=0.0)
    relevance_reasons = Column(JSON)        # list[str]
    is_relevant = Column(Boolean, nullable=False, default=False, index=True)

    probable_company = Column(Text, index=True)
    probable_work_group = Column(Text, index=True)
    probable_crew = Column(Text)

    source_pdf_path = Column(Text)
    source_pdf_sha256 = Column(String(64))
    source_html_path = Column(Text)

    parsed_at = Column(DateTime)
    parse_method = Column(String(32))

    # Latest content hash — used to detect changes
    latest_content_hash = Column(String(64), index=True)

    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

    # Relationships
    snapshots = relationship("ORMTicketSnapshot", back_populates="ticket", order_by="ORMTicketSnapshot.captured_at")
    reminder_events = relationship("ORMReminderEvent", back_populates="ticket")


class ORMTicketSnapshot(Base):
    """
    Immutable snapshot of a ticket's normalized payload at a point in time.

    A new snapshot is appended only when the content hash changes.
    This gives a full audit trail without storing duplicate data.
    """
    __tablename__ = "ticket_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=False, index=True)
    content_hash = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)       # Full NormalizedTicket dict
    captured_at = Column(DateTime, nullable=False)

    ticket = relationship("ORMTicket", back_populates="snapshots")

    __table_args__ = (
        UniqueConstraint("ticket_id", "content_hash", name="uq_snapshot_hash"),
        Index("ix_snapshot_ticket_captured", "ticket_id", "captured_at"),
    )


class ORMReminderEvent(Base):
    """
    Record of a reminder email sent for a ticket.

    Used to prevent duplicate reminders within the same lead-day window.
    """
    __tablename__ = "reminder_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=False, index=True)
    ticket_number = Column(String(64), nullable=False)
    sent_at = Column(DateTime, nullable=False)
    recipients = Column(JSON, nullable=False)    # list[str]
    expiration_date = Column(String(16))
    dry_run = Column(Boolean, nullable=False, default=False)
    lead_days = Column(Integer, nullable=False, default=4)

    ticket = relationship("ORMTicket", back_populates="reminder_events")

    __table_args__ = (
        # One reminder per ticket per lead_days bucket (prevent duplicates)
        UniqueConstraint("ticket_number", "expiration_date", "lead_days", "dry_run",
                         name="uq_reminder_bucket"),
    )


class ORMScrapeRun(Base):
    """
    Record of a scrape job execution.

    Captures outcome, counts, and any errors for operational monitoring.
    """
    __tablename__ = "scrape_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime)
    status = Column(String(16), nullable=False, default="started")
    counties = Column(JSON)                     # list[str]
    tickets_found = Column(Integer, default=0)
    tickets_new = Column(Integer, default=0)
    tickets_changed = Column(Integer, default=0)
    tickets_unchanged = Column(Integer, default=0)
    pdfs_downloaded = Column(Integer, default=0)
    parse_failures = Column(Integer, default=0)
    error_detail = Column(Text)
    dry_run = Column(Boolean, nullable=False, default=False)


class ORMParseFailure(Base):
    """
    Record of a parse failure requiring manual review.
    """
    __tablename__ = "parse_failures"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_number = Column(String(64), index=True)
    county = Column(String(64))
    reason = Column(String(64), nullable=False)
    detail = Column(Text)
    raw_url = Column(Text)
    raw_path = Column(Text)
    occurred_at = Column(DateTime, nullable=False)
    resolved = Column(Boolean, nullable=False, default=False)
    resolved_at = Column(DateTime)


class ORMNashDigsProject(Base):
    """
    Cached Northstar-owned fiber project pulled from NashDigs.

    NashDigs is Metro Nashville's public project-coordination FeatureServer.
    Northstar files each fiber package under the BNA### naming scheme before
    811 tickets are called in. One row per ProjectID (stable over time).
    Geometry is a GeoJSON MultiLineString in WGS84 for direct use with
    shapely after reprojection.
    """
    __tablename__ = "nashdigs_projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Upstream identifiers
    project_id = Column(Integer, nullable=False, unique=True, index=True)
    project_name = Column(String(64), nullable=False, index=True)  # BNA###

    # Descriptive fields
    facility_type = Column(String(32))
    project_type = Column(String(64))
    description = Column(Text)
    owner = Column(String(64), nullable=False, index=True)
    department = Column(String(64))
    division = Column(String(64))
    status = Column(String(32), index=True)
    council_district = Column(String(32))

    # Dates (ISO strings for consistency with other date fields on ORMTicket)
    est_start_date = Column(String(16))
    est_end_date = Column(String(16))
    act_start_date = Column(String(16))
    act_end_date = Column(String(16))

    # Geometry — GeoJSON MultiLineString in WGS84. Keeping full fidelity
    # (no precomputed shapely object) so we can reproject on demand.
    geometry_geojson = Column(JSON, nullable=False)

    # Audit
    fetched_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False)


class ORMInferredWorkGroup(Base):
    """
    Inferred work group — a cluster of related tickets sharing a probable company/crew.

    Not tied to a specific named subcontractor; derived from field similarity.
    """
    __tablename__ = "inferred_work_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_key = Column(String(128), nullable=False, unique=True, index=True)
    probable_company = Column(Text)
    probable_crew = Column(Text)
    county = Column(String(64))
    ticket_count = Column(Integer, default=0)
    first_seen = Column(DateTime)
    last_seen = Column(DateTime)
    representative_ticket = Column(String(64))  # ticket_number of best example

    __table_args__ = (
        Index("ix_work_group_company", "probable_company"),
    )


# ── ORM ↔ Pydantic conversion helpers ────────────────────────────────────────

def orm_to_normalized(t: ORMTicket) -> NormalizedTicket:
    """Convert an ORMTicket row to a NormalizedTicket schema."""
    from datetime import date as date_type

    def _parse_date(s: str | None) -> date_type | None:
        if not s:
            return None
        try:
            return date_type.fromisoformat(s)
        except ValueError:
            return None

    return NormalizedTicket(
        ticket_number=t.ticket_number,
        county=t.county,
        state=t.state or "TN",
        call_date=_parse_date(t.call_date),
        legal_start_date=_parse_date(t.legal_start_date),
        expiration_date=_parse_date(t.expiration_date),
        excavator_name=t.excavator_name,
        caller_name=t.caller_name,
        caller_phone=t.caller_phone,
        work_type=t.work_type,
        location_text=t.location_text,
        intersection_text=t.intersection_text,
        remarks=t.remarks,
        done_for=t.done_for,
        latitude=t.latitude,
        longitude=t.longitude,
        secondary_latitude=t.secondary_latitude,
        secondary_longitude=t.secondary_longitude,
        utility_references=t.utility_references or [],
        utility_responses=[UtilityResponse(**r) for r in (t.utility_responses or [])],
        utility_codes=t.utility_codes or [],
        utility_statuses=t.utility_statuses or [],
        is_ready_to_dig=bool(t.is_ready_to_dig),
        has_late_utility=bool(t.has_late_utility),
        late_utility_codes=t.late_utility_codes or [],
        pending_utility_codes=t.pending_utility_codes or [],
        blocking_utility_codes=t.blocking_utility_codes or [],
        status=TicketStatus(t.status) if t.status else TicketStatus.UNKNOWN,
        is_cancelled=bool(t.is_cancelled),
        relevance_score=float(t.relevance_score or 0.0),
        relevance_reasons=t.relevance_reasons or [],
        is_relevant=bool(t.is_relevant),
        probable_company=t.probable_company,
        probable_work_group=t.probable_work_group,
        probable_crew=t.probable_crew,
        source_pdf_path=t.source_pdf_path,
        source_pdf_sha256=t.source_pdf_sha256,
        source_html_path=t.source_html_path,
        parsed_at=t.parsed_at,
        parse_method=t.parse_method,
    )


def normalized_to_orm_dict(ticket: NormalizedTicket, now: datetime) -> dict[str, Any]:
    """Convert a NormalizedTicket to a dict suitable for ORMTicket upsert."""
    return {
        "ticket_number": ticket.ticket_number,
        "county": ticket.county,
        "state": ticket.state,
        "call_date": ticket.call_date.isoformat() if ticket.call_date else None,
        "legal_start_date": ticket.legal_start_date.isoformat() if ticket.legal_start_date else None,
        "expiration_date": ticket.expiration_date.isoformat() if ticket.expiration_date else None,
        "excavator_name": ticket.excavator_name,
        "caller_name": ticket.caller_name,
        "caller_phone": ticket.caller_phone,
        "work_type": ticket.work_type,
        "location_text": ticket.location_text,
        "intersection_text": ticket.intersection_text,
        "remarks": ticket.remarks,
        "done_for": ticket.done_for,
        "latitude": ticket.latitude,
        "longitude": ticket.longitude,
        "secondary_latitude": ticket.secondary_latitude,
        "secondary_longitude": ticket.secondary_longitude,
        "utility_references": ticket.utility_references,
        "utility_responses": [r.model_dump() for r in ticket.utility_responses],
        "utility_codes": ticket.utility_codes,
        "utility_statuses": ticket.utility_statuses,
        "is_ready_to_dig": ticket.is_ready_to_dig,
        "has_late_utility": ticket.has_late_utility,
        "late_utility_codes": ticket.late_utility_codes,
        "pending_utility_codes": ticket.pending_utility_codes,
        "blocking_utility_codes": ticket.blocking_utility_codes,
        "status": ticket.status.value,
        "is_cancelled": ticket.is_cancelled,
        "relevance_score": ticket.relevance_score,
        "relevance_reasons": ticket.relevance_reasons,
        "is_relevant": ticket.is_relevant,
        "probable_company": ticket.probable_company,
        "probable_work_group": ticket.probable_work_group,
        "probable_crew": ticket.probable_crew,
        "source_pdf_path": ticket.source_pdf_path,
        "source_pdf_sha256": ticket.source_pdf_sha256,
        "source_html_path": ticket.source_html_path,
        "parsed_at": ticket.parsed_at,
        "parse_method": ticket.parse_method,
        "latest_content_hash": ticket.content_hash(),
        "updated_at": now,
    }
