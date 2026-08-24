"""
snapshots/build_dashboard_json.py — Static JSON generation for the dashboard.

Reads from the database and writes static JSON files to data/exports/.
The dashboard React app reads only these files — no live DB or portal calls.

Output files:
  summary.json         KPI counts
  tickets_active.json  Active relevant tickets
  tickets_expiring.json Tickets expiring in the next N days
  by_county.json       Per-county breakdowns
  by_work_group.json   Per-work-group breakdowns
  recent_changes.json  Recently changed tickets
  parse_failures.json  Parse failures needing review
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from tn811.config import AppConfig
from tn811.models import (
    ORMParseFailure,
    ORMTicket,
    ORMTicketSnapshot,
    TicketStatus,
    orm_to_normalized,
)

logger = logging.getLogger(__name__)


def _to_json_safe(obj):
    """Make an object JSON-serializable."""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if hasattr(obj, "value"):  # Enum
        return obj.value
    raise TypeError(f"Not serializable: {type(obj)}")


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=_to_json_safe)
    logger.info("Wrote JSON", extra={"path": str(path), "size": path.stat().st_size})


def _ticket_dict(t: ORMTicket) -> dict:
    """Serialize an ORMTicket to a JSON-safe dict for the dashboard."""
    return {
        "ticket_number": t.ticket_number,
        "county": t.county,
        "state": t.state,
        "call_date": t.call_date,
        "legal_start_date": t.legal_start_date,
        "expiration_date": t.expiration_date,
        "excavator_name": t.excavator_name,
        "caller_name": t.caller_name,
        "work_type": t.work_type,
        "location_text": t.location_text,
        "remarks": t.remarks,
        "done_for": t.done_for,
        "utility_codes": t.utility_codes or [],
        "status": t.status,
        "is_cancelled": t.is_cancelled,
        "relevance_score": t.relevance_score,
        "relevance_reasons": t.relevance_reasons or [],
        "is_relevant": t.is_relevant,
        "probable_company": t.probable_company,
        "probable_work_group": t.probable_work_group,
        "probable_crew": t.probable_crew,
        "parsed_at": t.parsed_at.isoformat() if t.parsed_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


def build_all(session: Session, config: AppConfig) -> None:
    """
    Build all dashboard JSON files from the current database state.

    Args:
        session: Active SQLAlchemy session.
        config:  AppConfig with paths and dashboard settings.
    """
    exports_dir = Path(config.paths.exports_dir)
    dash = config.dashboard
    now = datetime.now(timezone.utc)
    today = now.date()

    # ── Load relevant active tickets ─────────────────────────────────────────
    active_tickets: list[ORMTicket] = (
        session.query(ORMTicket)
        .filter(
            ORMTicket.is_relevant == True,  # noqa: E712
            ORMTicket.status == TicketStatus.ACTIVE.value,
        )
        .order_by(ORMTicket.expiration_date)
        .all()
    )

    # ── Active tickets ────────────────────────────────────────────────────────
    _write_json(
        exports_dir / "tickets_active.json",
        {
            "generated_at": now.isoformat(),
            "count": len(active_tickets),
            "tickets": [_ticket_dict(t) for t in active_tickets[: dash.recent_limit]],
        },
    )

    # ── Expiring soon ─────────────────────────────────────────────────────────
    from datetime import timedelta
    expiry_cutoff = (today + timedelta(days=dash.expiring_window_days)).isoformat()

    expiring: list[ORMTicket] = [
        t for t in active_tickets
        if t.expiration_date and t.expiration_date <= expiry_cutoff
    ]
    _write_json(
        exports_dir / "tickets_expiring.json",
        {
            "generated_at": now.isoformat(),
            "window_days": dash.expiring_window_days,
            "count": len(expiring),
            "tickets": [_ticket_dict(t) for t in expiring[: dash.expiring_limit]],
        },
    )

    # ── Summary / KPIs ─────────────────────────────────────────────────────────
    total_tickets = session.query(ORMTicket).count()
    total_relevant = session.query(ORMTicket).filter(
        ORMTicket.is_relevant == True  # noqa: E712
    ).count()
    total_cancelled = session.query(ORMTicket).filter(
        ORMTicket.status == TicketStatus.CANCELLED.value
    ).count()
    total_expired = session.query(ORMTicket).filter(
        ORMTicket.status == TicketStatus.EXPIRED.value
    ).count()
    total_failures = session.query(ORMParseFailure).filter(
        ORMParseFailure.resolved == False  # noqa: E712
    ).count()

    _write_json(
        exports_dir / "summary.json",
        {
            "generated_at": now.isoformat(),
            "total_tickets": total_tickets,
            "relevant": total_relevant,
            "active_relevant": len(active_tickets),
            "expiring_soon": len(expiring),
            "cancelled": total_cancelled,
            "expired": total_expired,
            "parse_failures_open": total_failures,
        },
    )

    # ── By county ─────────────────────────────────────────────────────────────
    county_map: dict[str, dict] = {}
    all_relevant: list[ORMTicket] = (
        session.query(ORMTicket)
        .filter(ORMTicket.is_relevant == True)  # noqa: E712
        .all()
    )
    for t in all_relevant:
        county = t.county or "Unknown"
        if county not in county_map:
            county_map[county] = {"county": county, "total": 0, "active": 0, "expiring_soon": 0}
        county_map[county]["total"] += 1
        if t.status == TicketStatus.ACTIVE.value:
            county_map[county]["active"] += 1
        if t.expiration_date and t.expiration_date <= expiry_cutoff and t.status == TicketStatus.ACTIVE.value:
            county_map[county]["expiring_soon"] += 1

    _write_json(
        exports_dir / "by_county.json",
        {
            "generated_at": now.isoformat(),
            "counties": list(county_map.values()),
        },
    )

    # ── By work group ─────────────────────────────────────────────────────────
    group_map: dict[str, dict] = {}
    for t in all_relevant:
        group = t.probable_work_group or t.probable_company or "Unassigned"
        if group not in group_map:
            group_map[group] = {
                "group": group,
                "probable_company": t.probable_company,
                "total": 0,
                "active": 0,
                "counties": set(),
            }
        group_map[group]["total"] += 1
        if t.status == TicketStatus.ACTIVE.value:
            group_map[group]["active"] += 1
        group_map[group]["counties"].add(t.county or "")

    # Serialize (sets → lists)
    group_list = []
    for g in group_map.values():
        g["counties"] = sorted(g["counties"])
        group_list.append(g)
    group_list.sort(key=lambda g: g["total"], reverse=True)

    _write_json(
        exports_dir / "by_work_group.json",
        {
            "generated_at": now.isoformat(),
            "groups": group_list,
        },
    )

    # ── Recent changes ────────────────────────────────────────────────────────
    # Tickets that have more than one snapshot (i.e., changed at least once)
    changed_tickets: list[ORMTicket] = (
        session.query(ORMTicket)
        .join(ORMTicketSnapshot)
        .filter(ORMTicket.is_relevant == True)  # noqa: E712
        .group_by(ORMTicket.id)
        .having(
            session.query(ORMTicketSnapshot)
            .filter(ORMTicketSnapshot.ticket_id == ORMTicket.id)
            .count() > 1
        )
        .order_by(ORMTicket.updated_at.desc())
        .limit(dash.changes_limit)
        .all()
    )

    _write_json(
        exports_dir / "recent_changes.json",
        {
            "generated_at": now.isoformat(),
            "count": len(changed_tickets),
            "tickets": [_ticket_dict(t) for t in changed_tickets],
        },
    )

    # ── Parse failures ────────────────────────────────────────────────────────
    failures: list[ORMParseFailure] = (
        session.query(ORMParseFailure)
        .filter(ORMParseFailure.resolved == False)  # noqa: E712
        .order_by(ORMParseFailure.occurred_at.desc())
        .limit(dash.failures_limit)
        .all()
    )

    _write_json(
        exports_dir / "parse_failures.json",
        {
            "generated_at": now.isoformat(),
            "count": len(failures),
            "failures": [
                {
                    "id": f.id,
                    "ticket_number": f.ticket_number,
                    "county": f.county,
                    "reason": f.reason,
                    "detail": f.detail,
                    "raw_url": f.raw_url,
                    "raw_path": f.raw_path,
                    "occurred_at": f.occurred_at.isoformat() if f.occurred_at else None,
                }
                for f in failures
            ],
        },
    )

    logger.info("Dashboard JSON build complete", extra={"exports_dir": str(exports_dir)})
