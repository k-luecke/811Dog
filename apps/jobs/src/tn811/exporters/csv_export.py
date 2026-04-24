"""
exporters/csv_export.py — Supervisor- and subcontractor-facing CSV bundle.

Produces nine master CSVs in the exports directory (static filenames, overwritten
each run) plus a MANIFEST.txt. When `sub_slices=True`, also emits a
`by_sub/<slug>/` folder per excavator with at least one active GFiber ticket,
each folder containing up to three files (active / expiring_7d / late_utilities).

Design:
  load_ticket_views(session, now_ct)
      → list[TicketView]  — one per GFiber-related ticket, fields denormalized
                            and derivations (days_until_expire, utility_summary,
                            status) precomputed against now_ct.
  write_exports(views, out_dir, sub_slices, now_ct)
      → ExportManifest   — deletes stale master CSVs + by_sub/ subtree, writes
                            fresh files, returns a manifest summarizing rows+size.

The DB layer and file-writing layer are split so unit tests can exercise the
writer without touching SQLite.
"""
from __future__ import annotations

import csv
import logging
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from tn811.models import (
    ORMTicket,
    UTIL_STATUS_CLEAR,
    UTIL_STATUS_DELAYED,
    UTIL_STATUS_LOCATED,
    UTIL_STATUS_NOT_CLEAR,
    UTIL_STATUS_PENDING,
)

logger = logging.getLogger(__name__)

CT = ZoneInfo("America/Chicago")

MASTER_COLUMNS: tuple[str, ...] = (
    "ticket_number",
    "call_date",
    "legal_start_date",
    "expire_date",
    "days_until_expire",
    "county",
    "excavator_name",
    "caller_name",
    "caller_phone",
    "work_type",
    "address",
    "intersection",
    "place",
    "done_for",
    "is_ready_to_dig",
    "has_late_utility",
    "late_utility_codes",
    "blocking_utility_codes",
    "pending_utility_codes",
    "utility_summary",
    "is_cancelled",
    "status",
    "remarks",
    "latitude",
    "longitude",
)

UTILITY_DETAIL_COLUMNS: tuple[str, ...] = (
    "ticket_number",
    "call_date",
    "days_since_call",
    "county",
    "excavator_name",
    "address",
    "intersection",
    "place",
    "done_for",
    "utility_code",
    "utility_name",
    "facility",
    "status",
    "last_response",
    "response_timestamp",
    "is_late",
    "ticket_is_cancelled",
)

BY_SUB_COLUMNS: tuple[str, ...] = (
    "excavator_name",
    "active_count",
    "expiring_4d_count",
    "expiring_7d_count",
    "late_utilities_count",
    "blocked_count",
    "cancelled_last_7d_count",
    "earliest_expire_date",
    "latest_call_date",
    "primary_county",
)

MASTER_FILES: tuple[str, ...] = (
    "gfiber_active.csv",
    "gfiber_expiring_4d.csv",
    "gfiber_expiring_7d.csv",
    "gfiber_late_utilities.csv",
    "gfiber_blocked.csv",
    "gfiber_new_24h.csv",
    "gfiber_by_sub.csv",
    "gfiber_utility_detail.csv",
    "gfiber_cancelled_7d.csv",
    "unassigned_tickets_for_review.csv",
)

UNASSIGNED_REVIEW_COLUMNS: tuple[str, ...] = (
    "ticket_number",
    "call_date",
    "expire_date",
    "days_until_expire",
    "county",
    "excavator_name",
    "address",
    "intersection",
    "latitude",
    "longitude",
    "candidate_project_name",
    "distance_m",
    "done_for",
    "utility_summary",
    "remarks",
)


# ── Data shape ────────────────────────────────────────────────────────────────


@dataclass
class TicketView:
    """Denormalized ticket + derivations. Pure-data; serializable directly to CSV."""

    ticket_number: str
    call_date: date | None
    legal_start_date: date | None
    expire_date: date | None
    days_until_expire: int | None  # negative if expired; None if no expire_date
    county: str
    excavator_name: str
    caller_name: str
    caller_phone: str
    work_type: str
    address: str
    intersection: str
    place: str
    done_for: str
    is_ready_to_dig: bool
    has_late_utility: bool
    late_utility_codes: list[str]
    blocking_utility_codes: list[str]
    pending_utility_codes: list[str]
    utility_summary: str
    is_cancelled: bool
    status: str  # active | cancelled | expiring_soon | expired | unknown
    remarks: str
    latitude: float | None = None
    longitude: float | None = None
    secondary_latitude: float | None = None
    secondary_longitude: float | None = None
    nashdigs_project_name: str | None = None
    nashdigs_match_confidence: str | None = None
    nashdigs_distance_m: float | None = None
    utility_statuses: list[dict] = field(default_factory=list)


@dataclass
class SubRollup:
    excavator_name: str
    active_count: int
    expiring_4d_count: int
    expiring_7d_count: int
    late_utilities_count: int
    blocked_count: int
    cancelled_last_7d_count: int
    earliest_expire_date: date | None
    latest_call_date: date | None
    primary_county: str


@dataclass
class FileSummary:
    name: str
    rows: int
    size_bytes: int


@dataclass
class ExportManifest:
    generated_at: datetime
    total_tickets_in_db: int
    total_gfiber_in_db: int
    call_date_min: date | None
    call_date_max: date | None
    master_files: list[FileSummary]
    sub_slice_count: int
    sub_slice_dirs: list[str]


# ── Top-level entry points ────────────────────────────────────────────────────


def export(
    session: Session,
    out_dir: Path | str,
    sub_slices: bool = False,
    now: datetime | None = None,
    desktop_mirror_path: Path | str | None = None,
) -> ExportManifest:
    """Query the DB, write the export bundle, and (optionally) mirror to a second dir.

    The mirror is a convenience for delivering the bundle to a Windows desktop
    folder over /mnt/c/. A failure to write the mirror does NOT raise — the
    in-repo write at `out_dir` is the source of truth; the mirror is best-effort.
    """
    now = now or datetime.now(timezone.utc)
    now_ct = now.astimezone(CT)

    out_path = Path(out_dir)
    views = load_ticket_views(session, now_ct)
    stats = _db_stats(session)
    manifest = write_exports(
        views=views,
        out_dir=out_path,
        sub_slices=sub_slices,
        now_ct=now_ct,
        db_stats=stats,
    )

    if desktop_mirror_path:
        mirror_to_desktop(out_path, Path(desktop_mirror_path))

    return manifest


def mirror_to_desktop(src_dir: Path, dest_dir: Path) -> bool:
    """Copy the export bundle from src_dir to dest_dir, replacing any prior bundle.

    Only files the exporter owns are touched in `dest_dir` (the nine master
    CSVs, MANIFEST.txt, and the by_sub/ subtree). Any unrelated files dropped
    into the mirror folder are left alone.

    Returns True on success, False on any filesystem error. Errors are logged at
    WARNING — never raised — so the primary in-repo write is never rolled back
    by a flaky mount or permissions hiccup on the Windows side.
    """
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(
            "Desktop mirror: cannot create destination — skipping",
            extra={"dest": str(dest_dir), "error": str(exc)},
        )
        return False

    # Clear owned paths
    try:
        for name in MASTER_FILES:
            p = dest_dir / name
            if p.exists():
                p.unlink()
        manifest = dest_dir / "MANIFEST.txt"
        if manifest.exists():
            manifest.unlink()
        by_sub_dest = dest_dir / "by_sub"
        if by_sub_dest.exists():
            shutil.rmtree(by_sub_dest)
    except OSError as exc:
        logger.warning(
            "Desktop mirror: failed to clear prior bundle — skipping",
            extra={"dest": str(dest_dir), "error": str(exc)},
        )
        return False

    # Copy fresh bundle
    try:
        for name in MASTER_FILES:
            src = src_dir / name
            if src.exists():
                shutil.copy2(src, dest_dir / name)
        src_manifest = src_dir / "MANIFEST.txt"
        if src_manifest.exists():
            shutil.copy2(src_manifest, dest_dir / "MANIFEST.txt")
        src_by_sub = src_dir / "by_sub"
        if src_by_sub.exists() and src_by_sub.is_dir():
            shutil.copytree(src_by_sub, dest_dir / "by_sub")
    except OSError as exc:
        logger.warning(
            "Desktop mirror: copy failed — primary write at src_dir is intact",
            extra={"src": str(src_dir), "dest": str(dest_dir), "error": str(exc)},
        )
        return False

    logger.info(
        "Desktop mirror: bundle copied",
        extra={"src": str(src_dir), "dest": str(dest_dir)},
    )
    return True


def load_ticket_views(session: Session, now_ct: datetime) -> list[TicketView]:
    """Load all GFiber-related tickets from the DB and compute per-ticket derivations."""
    tickets: list[ORMTicket] = (
        session.query(ORMTicket).filter(ORMTicket.is_gfiber_related == True).all()  # noqa: E712
    )
    return [ticket_view_from_orm(t, now_ct) for t in tickets]


def ticket_view_from_orm(t: ORMTicket, now_ct: datetime) -> TicketView:
    """Convert a single ORMTicket row into a denormalized, CSV-ready view."""
    call_date = _parse_iso_date(t.call_date)
    legal_start_date = _parse_iso_date(t.legal_start_date)
    expire_date = _parse_iso_date(t.expiration_date)

    today_ct = now_ct.date()
    days_until_expire = (expire_date - today_ct).days if expire_date else None

    is_cancelled = bool(t.is_cancelled)
    utility_statuses = list(t.utility_statuses or [])

    status = _derive_display_status(
        is_cancelled=is_cancelled,
        expire_date=expire_date,
        today=today_ct,
    )

    return TicketView(
        ticket_number=t.ticket_number or "",
        call_date=call_date,
        legal_start_date=legal_start_date,
        expire_date=expire_date,
        days_until_expire=days_until_expire,
        county=t.county or "",
        excavator_name=t.excavator_name or "",
        caller_name=t.caller_name or "",
        caller_phone=t.caller_phone or "",
        work_type=t.work_type or "",
        address=t.location_text or "",
        intersection=t.intersection_text or "",
        place="",  # Not extracted separately; city lives inside address
        done_for=t.done_for or "",
        is_ready_to_dig=bool(t.is_ready_to_dig),
        has_late_utility=bool(t.has_late_utility),
        late_utility_codes=list(t.late_utility_codes or []),
        blocking_utility_codes=list(t.blocking_utility_codes or []),
        pending_utility_codes=list(t.pending_utility_codes or []),
        utility_summary=utility_summary(utility_statuses),
        is_cancelled=is_cancelled,
        status=status,
        remarks=t.remarks or "",
        latitude=t.latitude,
        longitude=t.longitude,
        secondary_latitude=t.secondary_latitude,
        secondary_longitude=t.secondary_longitude,
        nashdigs_project_name=getattr(t, "nashdigs_project_name", None),
        nashdigs_match_confidence=getattr(t, "nashdigs_match_confidence", None),
        nashdigs_distance_m=getattr(t, "nashdigs_distance_m", None),
        utility_statuses=utility_statuses,
    )


# ── Writer ────────────────────────────────────────────────────────────────────


def write_exports(
    views: Sequence[TicketView],
    out_dir: Path,
    sub_slices: bool,
    now_ct: datetime,
    db_stats: dict | None = None,
) -> ExportManifest:
    """Delete stale outputs, write fresh CSVs, return a manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _clear_stale_outputs(out_dir)

    today = now_ct.date()
    cutoff_4d = today + timedelta(days=4)
    cutoff_7d = today + timedelta(days=7)
    call_cutoff_24h = today - timedelta(days=1)  # today or yesterday
    call_cutoff_7d = today - timedelta(days=7)

    def active_mask(v: TicketView) -> bool:
        return not v.is_cancelled

    active = [v for v in views if active_mask(v)]
    expiring_4d = [
        v for v in active if v.expire_date and v.expire_date <= cutoff_4d
    ]
    expiring_7d = [
        v for v in active if v.expire_date and v.expire_date <= cutoff_7d
    ]
    late_utilities = [v for v in active if v.has_late_utility]
    blocked = [v for v in active if v.blocking_utility_codes]
    new_24h = [v for v in active if v.call_date and v.call_date >= call_cutoff_24h]
    cancelled_7d = [
        v for v in views
        if v.is_cancelled and v.call_date and v.call_date >= call_cutoff_7d
    ]

    # Sort master outputs: by expire_date asc (None last), then ticket_number.
    def _sort_master(rows: list[TicketView]) -> list[TicketView]:
        return sorted(
            rows,
            key=lambda v: (
                v.expire_date is None,
                v.expire_date or date.max,
                v.ticket_number,
            ),
        )

    master_pairs: list[tuple[str, list[TicketView]]] = [
        ("gfiber_active.csv", _sort_master(active)),
        ("gfiber_expiring_4d.csv", _sort_master(expiring_4d)),
        ("gfiber_expiring_7d.csv", _sort_master(expiring_7d)),
        ("gfiber_late_utilities.csv", _sort_master(late_utilities)),
        ("gfiber_blocked.csv", _sort_master(blocked)),
        ("gfiber_new_24h.csv", _sort_master(new_24h)),
        ("gfiber_cancelled_7d.csv", _sort_master(cancelled_7d)),
    ]

    summaries: list[FileSummary] = []
    for name, rows in master_pairs:
        path = out_dir / name
        _write_master_csv(path, rows)
        summaries.append(_summarize_file(path, len(rows)))

    # By-sub rollup
    rollups = compute_sub_rollups(views, today)
    by_sub_path = out_dir / "gfiber_by_sub.csv"
    _write_by_sub_csv(by_sub_path, rollups)
    summaries.append(_summarize_file(by_sub_path, len(rollups)))

    # Utility detail — one row per utility per non-cancelled GFiber ticket
    detail_path = out_dir / "gfiber_utility_detail.csv"
    detail_row_count = _write_utility_detail_csv(detail_path, active, today)
    summaries.append(_summarize_file(detail_path, detail_row_count))

    # NashDigs low-confidence tickets — manual review list
    review_path = out_dir / "unassigned_tickets_for_review.csv"
    review_rows = [v for v in active if v.nashdigs_match_confidence == "low"]
    _write_unassigned_review_csv(review_path, review_rows)
    summaries.append(_summarize_file(review_path, len(review_rows)))

    # Stable manifest order matches MASTER_FILES
    summaries = _order_summaries(summaries)

    sub_dirs: list[str] = []
    if sub_slices:
        sub_dirs = _write_sub_slices(out_dir, views, cutoff_7d)

    manifest = ExportManifest(
        generated_at=now_ct,
        total_tickets_in_db=(db_stats or {}).get("total_tickets", 0),
        total_gfiber_in_db=(db_stats or {}).get("total_gfiber", len(views)),
        call_date_min=(db_stats or {}).get("call_date_min"),
        call_date_max=(db_stats or {}).get("call_date_max"),
        master_files=summaries,
        sub_slice_count=len(sub_dirs),
        sub_slice_dirs=sub_dirs,
    )
    _write_manifest(out_dir / "MANIFEST.txt", manifest)
    return manifest


# ── Helpers — derivations ─────────────────────────────────────────────────────


# Maps the portal's `last_response` text on a blocked utility row to the
# short label we emit in `utility_summary`. Any response text not in this
# map falls back to the generic "blocked" label.
_BLOCKED_REASON_LABELS: dict[str, str] = {
    "locate delayed": "delayed",
    "in conflict": "in conflict",
    "cannot locate - contact utility": "cannot locate",
    "no response": "no response",
}

# Order in which blocked sub-groups appear in the summary string. Most
# actionable reasons surface first so a supervisor scanning the column
# sees genuine conflicts before mere delays (which dominate by volume).
_BLOCKED_REASON_ORDER: tuple[str, ...] = (
    "in conflict",
    "cannot locate",
    "no response",
    "delayed",
    "blocked",  # fallback bucket for unmapped response text
)


def _blocked_reason(last_response: str | None) -> str:
    if not last_response:
        return "blocked"
    key = " ".join(last_response.strip().lower().split())
    return _BLOCKED_REASON_LABELS.get(key, "blocked")


def utility_summary(statuses: Iterable[dict]) -> str:
    """Human-readable one-line summary of the utility response mix for a ticket.

    Blocked utilities are grouped by the specific portal response reason
    (delayed / in conflict / cannot locate / no response) rather than
    collapsed into a single "blocked" bucket — a Locate-Delayed utility is
    usually a wait, while In-Conflict or Cannot-Locate require active
    coordination. Codes are named for late and for each blocked sub-group;
    pending codes are counted but not individually named. Statuses not
    mapped to clear/late/pending/blocked are skipped silently.
    """
    statuses = list(statuses or [])
    if not statuses:
        return ""

    clear_count = 0
    late_codes: list[str] = []
    blocked_by_reason: dict[str, list[str]] = {}
    pending_count = 0

    for s in statuses:
        code = str(s.get("code") or "").strip()
        status = s.get("status", "unknown")
        if status in (UTIL_STATUS_CLEAR, UTIL_STATUS_LOCATED):
            clear_count += 1
        elif s.get("is_late"):
            if code:
                late_codes.append(code)
        elif status == UTIL_STATUS_PENDING:
            pending_count += 1
        elif status in (UTIL_STATUS_DELAYED, UTIL_STATUS_NOT_CLEAR):
            if not code:
                continue
            reason = _blocked_reason(s.get("last_response"))
            blocked_by_reason.setdefault(reason, []).append(code)
        # unknown: ignore in summary

    parts: list[str] = []
    if clear_count:
        parts.append(f"{clear_count} clear")
    if late_codes:
        parts.append(f"{len(late_codes)} late ({', '.join(late_codes)})")
    for reason in _BLOCKED_REASON_ORDER:
        if reason in blocked_by_reason:
            codes = blocked_by_reason[reason]
            parts.append(f"{len(codes)} {reason} ({', '.join(codes)})")
    if pending_count:
        parts.append(f"{pending_count} pending")
    return ", ".join(parts)


def slugify(name: str) -> str:
    """Lowercase, collapse non-alphanumerics to dashes, trim. Empty → 'unknown'."""
    if not name:
        return "unknown"
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s)
    s = s.strip("-")
    return s or "unknown"


def _group_by_slug(
    views: Sequence[TicketView],
) -> dict[str, tuple[str, list[TicketView]]]:
    """Group tickets by excavator slug. Returns {slug: (canonical_name, views)}.

    Two raw excavator names that slugify to the same value are merged into one
    bucket — otherwise they collide on disk (second folder overwrites first) and
    double-count in the rollup CSV. The canonical display name is the most
    frequent raw spelling within the group.
    """
    buckets: dict[str, list[TicketView]] = {}
    for v in views:
        name = (v.excavator_name or "").strip()
        if not name:
            continue
        buckets.setdefault(slugify(name), []).append(v)

    result: dict[str, tuple[str, list[TicketView]]] = {}
    for slug, group in buckets.items():
        name_counts = Counter(v.excavator_name.strip() for v in group)
        canonical = name_counts.most_common(1)[0][0]
        result[slug] = (canonical, group)
    return result


def compute_sub_rollups(
    views: Sequence[TicketView],
    today: date,
) -> list[SubRollup]:
    """Group by excavator slug and produce sorted rollups (one row per slug)."""
    by_slug = _group_by_slug(views)

    cutoff_4d = today + timedelta(days=4)
    cutoff_7d = today + timedelta(days=7)
    call_cutoff_7d = today - timedelta(days=7)

    rollups: list[SubRollup] = []
    for slug, (name, sub_views) in by_slug.items():
        active = [v for v in sub_views if not v.is_cancelled]
        if not active:
            # Still emit a row if they had recent cancellations? Spec: folder when
            # active_count >= 1. For the rollup CSV itself, omit zero-active subs.
            continue

        expiring_4d = [v for v in active if v.expire_date and v.expire_date <= cutoff_4d]
        expiring_7d = [v for v in active if v.expire_date and v.expire_date <= cutoff_7d]
        late = [v for v in active if v.has_late_utility]
        blocked = [v for v in active if v.blocking_utility_codes]
        cancelled_7d = [
            v for v in sub_views
            if v.is_cancelled and v.call_date and v.call_date >= call_cutoff_7d
        ]

        expire_dates = [v.expire_date for v in active if v.expire_date]
        call_dates = [v.call_date for v in sub_views if v.call_date]
        county_counts = Counter(v.county for v in sub_views if v.county)
        primary_county = county_counts.most_common(1)[0][0] if county_counts else ""

        rollups.append(
            SubRollup(
                excavator_name=name,
                active_count=len(active),
                expiring_4d_count=len(expiring_4d),
                expiring_7d_count=len(expiring_7d),
                late_utilities_count=len(late),
                blocked_count=len(blocked),
                cancelled_last_7d_count=len(cancelled_7d),
                earliest_expire_date=min(expire_dates) if expire_dates else None,
                latest_call_date=max(call_dates) if call_dates else None,
                primary_county=primary_county,
            )
        )

    rollups.sort(key=lambda r: (-r.active_count, r.excavator_name))
    return rollups


# ── Helpers — CSV writing ─────────────────────────────────────────────────────


def _write_master_csv(path: Path, views: Sequence[TicketView]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(MASTER_COLUMNS)
        for v in views:
            writer.writerow(_view_to_master_row(v))


def _write_by_sub_csv(path: Path, rollups: Sequence[SubRollup]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(BY_SUB_COLUMNS)
        for r in rollups:
            writer.writerow(
                [
                    r.excavator_name,
                    r.active_count,
                    r.expiring_4d_count,
                    r.expiring_7d_count,
                    r.late_utilities_count,
                    r.blocked_count,
                    r.cancelled_last_7d_count,
                    _fmt_date(r.earliest_expire_date),
                    _fmt_date(r.latest_call_date),
                    r.primary_county,
                ]
            )


def _write_unassigned_review_csv(
    path: Path,
    views: Sequence[TicketView],
) -> None:
    """Tickets with NashDigs match_confidence == 'low' — supervisor review list.

    Sorted by distance ascending so the closest-but-still-uncertain matches
    float to the top of the file for quick triage.
    """
    sorted_views = sorted(
        views,
        key=lambda v: (v.nashdigs_distance_m is None, v.nashdigs_distance_m or 0.0),
    )
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(UNASSIGNED_REVIEW_COLUMNS)
        for v in sorted_views:
            writer.writerow(
                [
                    v.ticket_number,
                    _fmt_date(v.call_date),
                    _fmt_date(v.expire_date),
                    "" if v.days_until_expire is None else v.days_until_expire,
                    v.county,
                    v.excavator_name,
                    v.address,
                    v.intersection,
                    "" if v.latitude is None else f"{v.latitude:.6f}",
                    "" if v.longitude is None else f"{v.longitude:.6f}",
                    v.nashdigs_project_name or "",
                    "" if v.nashdigs_distance_m is None else f"{v.nashdigs_distance_m:.1f}",
                    v.done_for,
                    v.utility_summary,
                    v.remarks,
                ]
            )


def _write_utility_detail_csv(
    path: Path,
    active_views: Sequence[TicketView],
    today: date,
) -> int:
    """One row per utility per non-cancelled GFiber ticket. Returns row count."""
    rows: list[tuple[TicketView, dict]] = []
    for v in active_views:
        for u in v.utility_statuses or []:
            rows.append((v, u))
    rows.sort(
        key=lambda p: (p[0].ticket_number, str(p[1].get("code") or ""))
    )

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(UTILITY_DETAIL_COLUMNS)
        for v, u in rows:
            days_since = (today - v.call_date).days if v.call_date else ""
            writer.writerow(
                [
                    v.ticket_number,
                    _fmt_date(v.call_date),
                    days_since,
                    v.county,
                    v.excavator_name,
                    v.address,
                    v.intersection,
                    v.place,
                    v.done_for,
                    u.get("code") or "",
                    u.get("name") or "",
                    u.get("facility") or "",
                    u.get("status") or "",
                    u.get("last_response") or "",
                    u.get("response_timestamp") or "",
                    _fmt_bool(bool(u.get("is_late"))),
                    _fmt_bool(v.is_cancelled),
                ]
            )
    return len(rows)


def _write_sub_slices(
    out_dir: Path,
    views: Sequence[TicketView],
    cutoff_7d: date,
) -> list[str]:
    """Emit by_sub/<slug>/{active,expiring_7d,late_utilities}.csv per active excavator."""
    by_sub_dir = out_dir / "by_sub"
    by_sub_dir.mkdir(parents=True, exist_ok=True)

    by_slug = _group_by_slug(views)

    dirs: list[str] = []
    for slug in sorted(by_slug):
        _, sub_views = by_slug[slug]
        active = [v for v in sub_views if not v.is_cancelled]
        if not active:
            continue

        sub_dir = by_sub_dir / slug
        sub_dir.mkdir(parents=True, exist_ok=True)

        _write_master_csv(sub_dir / "active.csv", _sorted_by_expire(active))

        expiring = [v for v in active if v.expire_date and v.expire_date <= cutoff_7d]
        if expiring:
            _write_master_csv(sub_dir / "expiring_7d.csv", _sorted_by_expire(expiring))

        late = [v for v in active if v.has_late_utility]
        if late:
            _write_master_csv(sub_dir / "late_utilities.csv", _sorted_by_expire(late))

        dirs.append(f"by_sub/{slug}")

    return dirs


def _sorted_by_expire(views: Sequence[TicketView]) -> list[TicketView]:
    return sorted(
        views,
        key=lambda v: (
            v.expire_date is None,
            v.expire_date or date.max,
            v.ticket_number,
        ),
    )


def _view_to_master_row(v: TicketView) -> list:
    return [
        v.ticket_number,
        _fmt_date(v.call_date),
        _fmt_date(v.legal_start_date),
        _fmt_date(v.expire_date),
        "" if v.days_until_expire is None else v.days_until_expire,
        v.county,
        v.excavator_name,
        v.caller_name,
        v.caller_phone,
        v.work_type,
        v.address,
        v.intersection,
        v.place,
        v.done_for,
        _fmt_bool(v.is_ready_to_dig),
        _fmt_bool(v.has_late_utility),
        ",".join(v.late_utility_codes),
        ",".join(v.blocking_utility_codes),
        ",".join(v.pending_utility_codes),
        v.utility_summary,
        _fmt_bool(v.is_cancelled),
        v.status,
        v.remarks,
        "" if v.latitude is None else f"{v.latitude:.6f}",
        "" if v.longitude is None else f"{v.longitude:.6f}",
    ]


# ── Helpers — formatting ──────────────────────────────────────────────────────


def _fmt_date(d: date | None) -> str:
    return d.isoformat() if d else ""


def _fmt_bool(b: bool) -> str:
    return "TRUE" if b else "FALSE"


def _parse_iso_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _derive_display_status(
    *, is_cancelled: bool, expire_date: date | None, today: date
) -> str:
    if is_cancelled:
        return "cancelled"
    if expire_date is None:
        return "unknown"
    if expire_date < today:
        return "expired"
    if (expire_date - today).days <= 7:
        return "expiring_soon"
    return "active"


# ── Helpers — IO ──────────────────────────────────────────────────────────────


def _clear_stale_outputs(out_dir: Path) -> None:
    """Delete stale master CSVs, by_sub/ subtree, and MANIFEST.txt before a run."""
    for name in MASTER_FILES:
        p = out_dir / name
        if p.exists():
            p.unlink()
    manifest_path = out_dir / "MANIFEST.txt"
    if manifest_path.exists():
        manifest_path.unlink()
    by_sub_dir = out_dir / "by_sub"
    if by_sub_dir.exists():
        shutil.rmtree(by_sub_dir)


def _summarize_file(path: Path, rows: int) -> FileSummary:
    size = path.stat().st_size if path.exists() else 0
    return FileSummary(name=path.name, rows=rows, size_bytes=size)


def _order_summaries(summaries: list[FileSummary]) -> list[FileSummary]:
    by_name = {s.name: s for s in summaries}
    return [by_name[n] for n in MASTER_FILES if n in by_name]


def _write_manifest(path: Path, manifest: ExportManifest) -> None:
    lines: list[str] = []
    lines.append(
        "Generated at: "
        f"{manifest.generated_at.strftime('%Y-%m-%d %H:%M:%S')} "
        f"{manifest.generated_at.tzname() or ''}".rstrip()
    )
    call_range = (
        f"{_fmt_date(manifest.call_date_min)} to {_fmt_date(manifest.call_date_max)}"
        if manifest.call_date_min and manifest.call_date_max
        else "(no tickets)"
    )
    lines.append(
        f"DB snapshot: {manifest.total_tickets_in_db:,} total tickets, "
        f"{manifest.total_gfiber_in_db:,} GFiber, "
        f"call_date range {call_range}"
    )
    lines.append("")
    lines.append("Master files:")
    name_w = max((len(f.name) for f in manifest.master_files), default=0)
    for f in manifest.master_files:
        lines.append(
            f"  {f.name:<{name_w}}  {f.rows:>7,} rows  {_fmt_size(f.size_bytes):>9}"
        )
    lines.append("")
    if manifest.sub_slice_count:
        lines.append(f"Per-subcontractor slices ({manifest.sub_slice_count} subs):")
        for d in manifest.sub_slice_dirs:
            lines.append(f"  {d}/")
    else:
        lines.append("Per-subcontractor slices: none (pass --sub-slices to generate)")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _db_stats(session: Session) -> dict:
    """Cheap aggregate stats used to populate the manifest header."""
    total = session.query(ORMTicket).count()
    gfiber = (
        session.query(ORMTicket)
        .filter(ORMTicket.is_gfiber_related == True)  # noqa: E712
        .count()
    )
    min_call = (
        session.query(ORMTicket.call_date)
        .filter(ORMTicket.call_date.isnot(None))
        .order_by(ORMTicket.call_date.asc())
        .limit(1)
        .scalar()
    )
    max_call = (
        session.query(ORMTicket.call_date)
        .filter(ORMTicket.call_date.isnot(None))
        .order_by(ORMTicket.call_date.desc())
        .limit(1)
        .scalar()
    )
    return {
        "total_tickets": total,
        "total_gfiber": gfiber,
        "call_date_min": _parse_iso_date(min_call),
        "call_date_max": _parse_iso_date(max_call),
    }
