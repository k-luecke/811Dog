"""
exporters/kmz_export.py — Google Earth (Pro) operational view.

Produces a single .kmz bundle with a three-axis layer hierarchy so the
supervisor can toggle between views in Google Earth's Places panel:

  Meridian Ops Snapshot — <timestamp>
  ├── Active relevant — by urgency           (visible by default)
  │   ├── Expiring ≤ 4 days            (red pins)
  │   ├── Expiring 5–7 days            (orange pins)
  │   ├── Expiring 8+ days             (blue pins)
  │   └── No expire date               (gray pins)
  ├── Active relevant — by utility status    (hidden)
  │   ├── Ready to dig                 (green)
  │   ├── Pending utilities            (yellow)
  │   ├── Has late utilities           (orange)
  │   ├── Blocked — delayed            (orange-red)
  │   └── Blocked — conflict/other     (dark red)
  ├── Active relevant — by sub               (hidden)
  │   └── <one folder per excavator>   (deterministic per-sub color)
  ├── Cancelled relevant — last 14 days      (hidden)
  └── Meridian non-relevant                     (hidden, only if N > 0)

Each active relevant ticket is rendered three times — once per axis — so the
layer toggles in Google Earth work independently. Tickets with late or
blocking utilities use a caution icon regardless of folder so they stand
out visually.

Geometry: if primary + secondary coords are >50 m apart (haversine), the
ticket renders as a LineString (dig path) plus a Point at the midpoint
for the clickable pin. Otherwise a single Point at primary lat/lon.
Tickets with no coords are skipped.
"""
from __future__ import annotations

import hashlib
import html
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import simplekml
from jinja2 import Environment
from sqlalchemy.orm import Session

from tn811.connectors.nashdigs import NashDigsPackage, load_cached
from tn811.exporters.csv_export import (
    CT,
    TicketView,
    _blocked_reason,
    _group_by_slug,
    ticket_view_from_orm,
)
from tn811.models import (
    ORMNashDigsProject,
    ORMTicket,
    UTIL_STATUS_DELAYED,
    UTIL_STATUS_NOT_CLEAR,
)

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

HAVERSINE_THRESHOLD_M = 50.0
CAUTION_ICON_URL = "http://maps.google.com/mapfiles/kml/shapes/caution.png"
DEFAULT_OUT_PATH = Path("data/exports/ops_snapshot.kmz")

# Dig-zone circle approximates the TN811 coordinate's practical accuracy.
# TN811 geocodes differ from Google Earth imagery alignment by ~20–50 m in urban
# Davidson/Rutherford (investigation 2026-04-24); 25 m is a conservative
# visual-tolerance ring that covers the typical miss without over-claiming.
CIRCLE_RADIUS_M = 25.0
CIRCLE_VERTICES = 32
CIRCLE_FILL_ALPHA = 0.20
CIRCLE_OUTLINE_ALPHA = 0.60

# Reserved RGB triples that sub-color hashing must avoid.
_RESERVED_RGB: tuple[tuple[int, int, int], ...] = (
    (255, 0, 0),       # red
    (255, 165, 0),     # orange
    (0, 0, 255),       # blue
    (128, 128, 128),   # gray
    (0, 255, 0),       # green
    (255, 255, 0),     # yellow
    (255, 69, 0),      # orange-red
    (139, 0, 0),       # dark red
    (128, 0, 128),     # purple
    (255, 255, 255),   # white (unreadable)
    (0, 0, 0),         # black (unreadable)
)
_RESERVED_MIN_DIST = 80.0  # Euclidean RGB distance under which we perturb


# ── Popup HTML (Jinja2) ───────────────────────────────────────────────────────

_POPUP_TEMPLATE_SRC = """\
<h3>Ticket {{ ticket_number }}</h3>
<table>
  <tr><td><b>Excavator:</b></td><td>{{ excavator_name }}</td></tr>
  <tr><td><b>Work Type:</b></td><td>{{ work_type }}</td></tr>
  <tr><td><b>Done For:</b></td><td>{{ done_for }}</td></tr>
  <tr><td><b>Called In:</b></td><td>{{ call_date }}</td></tr>
  <tr><td><b>Expires:</b></td><td>{{ expire_date }}{% if days_until_expire is not none %} ({{ days_until_expire }} days){% endif %}</td></tr>
  <tr><td><b>County:</b></td><td>{{ county }}</td></tr>
  <tr><td><b>Location:</b></td><td>{{ address }}{% if intersection %} / {{ intersection }}{% endif %}</td></tr>
  <tr><td><b>Utility Status:</b></td><td>{{ utility_summary }}</td></tr>
  <tr><td><b>Ready to dig:</b></td><td>{{ "Yes" if is_ready_to_dig else "No" }}</td></tr>
</table>
{%- if remarks %}
<h4>Remarks:</h4>
<p>{{ remarks | nl2br }}</p>
{%- endif %}
{%- if utility_statuses %}
<h4>Utilities:</h4>
<table border="1" cellpadding="3">
  <tr><th>Code</th><th>Name</th><th>Facility</th><th>Status</th><th>Last Response</th></tr>
  {%- for u in utility_statuses %}
  <tr><td>{{ u.code or '' }}</td><td>{{ u.name or '' }}</td><td>{{ u.facility or '' }}</td><td>{{ u.status or '' }}</td><td>{{ u.last_response or '' }}</td></tr>
  {%- endfor %}
</table>
{%- endif %}
"""


def _nl2br(text) -> "Markup":
    """Escape user text, then convert newlines to <br>. Returns Markup so Jinja
    does not re-escape the <br> tags we just inserted."""
    from markupsafe import Markup, escape
    if text is None:
        return Markup("")
    return Markup(str(escape(str(text))).replace("\n", "<br>\n"))


_JINJA_ENV = Environment(autoescape=True)
_JINJA_ENV.filters["nl2br"] = _nl2br
_POPUP_TEMPLATE = _JINJA_ENV.from_string(_POPUP_TEMPLATE_SRC)


def _render_popup(view: TicketView) -> str:
    """Render the KML description HTML for a ticket. All user-provided text is
    autoescaped by Jinja; nl2br runs AFTER escape so <br> tags come through."""
    return _POPUP_TEMPLATE.render(
        ticket_number=view.ticket_number,
        excavator_name=view.excavator_name,
        work_type=view.work_type,
        done_for=view.done_for,
        call_date=view.call_date.isoformat() if view.call_date else "",
        expire_date=view.expire_date.isoformat() if view.expire_date else "",
        days_until_expire=view.days_until_expire,
        county=view.county,
        address=view.address,
        intersection=view.intersection,
        utility_summary=view.utility_summary,
        is_ready_to_dig=view.is_ready_to_dig,
        remarks=view.remarks,
        utility_statuses=view.utility_statuses,
    )


# ── Geometry helpers ──────────────────────────────────────────────────────────


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two (lat, lon) points."""
    r = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def _midpoint(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, float]:
    """Good enough at sub-kilometer dig-path scale; no great-circle correction."""
    return ((lat1 + lat2) / 2.0, (lon1 + lon2) / 2.0)


def circle_polygon(
    lat: float,
    lon: float,
    radius_m: float = CIRCLE_RADIUS_M,
    n_vertices: int = CIRCLE_VERTICES,
) -> list[tuple[float, float]]:
    """Return a closed ring of (lon, lat) tuples approximating a circle.

    Uses flat-earth metric approximation per-latitude: 1° latitude ≈ 111 320 m,
    1° longitude ≈ 111 320·cos(lat) m. At 25 m radius, the sub-cm deviation from
    a true geodesic circle is negligible — we intentionally avoid pulling in
    pyproj for this cosmetic ring.

    The returned ring is closed: ring[-1] == ring[0], as required by KML
    Polygon/outerBoundaryIs.
    """
    meters_per_deg_lat = 111_320.0
    meters_per_deg_lon = 111_320.0 * math.cos(math.radians(lat))
    dlat = radius_m / meters_per_deg_lat
    dlon = radius_m / meters_per_deg_lon

    ring: list[tuple[float, float]] = []
    for i in range(n_vertices):
        theta = 2.0 * math.pi * i / n_vertices
        vlat = lat + dlat * math.sin(theta)
        vlon = lon + dlon * math.cos(theta)
        ring.append((vlon, vlat))  # KML wants (lon, lat)
    ring.append(ring[0])  # close the ring
    return ring


def _is_point_only(view: TicketView) -> bool:
    """True iff this ticket would render as a single Point (not a LineString)."""
    if view.latitude is None or view.longitude is None:
        return False
    if view.secondary_latitude is None or view.secondary_longitude is None:
        return True
    return (
        haversine_m(
            view.latitude, view.longitude,
            view.secondary_latitude, view.secondary_longitude,
        )
        <= HAVERSINE_THRESHOLD_M
    )


def _apply_alpha(kml_color: str, alpha: float) -> str:
    """Recompose an AABBGGRR KML color string with a new alpha (0.0–1.0)."""
    alpha_byte = max(0, min(255, int(round(alpha * 255))))
    # simplekml colors are 8-char AABBGGRR; take the last 6 chars (BBGGRR) and prepend new AA
    return f"{alpha_byte:02x}{kml_color[-6:]}"


# ── Color helpers ─────────────────────────────────────────────────────────────


def _rgb_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _too_close_to_reserved(rgb: tuple[int, int, int]) -> bool:
    return any(_rgb_distance(rgb, res) < _RESERVED_MIN_DIST for res in _RESERVED_RGB)


def sub_color(name: str) -> str:
    """Deterministic KML color for a subcontractor name. Same input always
    yields the same color; result is guaranteed to be at least _RESERVED_MIN_DIST
    from every reserved urgency/status color.

    Uses MD5 as a stable hash (seeded perturbation if the raw hash is too close
    to a reserved color, so the output is still deterministic across runs).
    """
    seed = 0
    while True:
        digest = hashlib.md5(f"{name}|{seed}".encode("utf-8")).digest()
        rgb = (digest[0], digest[1], digest[2])
        if not _too_close_to_reserved(rgb):
            return simplekml.Color.rgb(*rgb)
        seed += 1
        if seed > 64:  # pathological case; fall through with a muted fallback
            return simplekml.Color.rgb(100, 100, 200)


# ── Bucket assignment ─────────────────────────────────────────────────────────


def urgency_bucket(view: TicketView) -> tuple[str, str]:
    """Return (bucket_label, simplekml color) for the urgency axis."""
    d = view.days_until_expire
    if d is None:
        return ("No expire date", simplekml.Color.gray)
    if d <= 4:
        return ("Expiring ≤ 4 days", simplekml.Color.red)
    if d <= 7:
        return ("Expiring 5–7 days", simplekml.Color.orange)
    return ("Expiring 8+ days", simplekml.Color.blue)


def utility_status_bucket(view: TicketView) -> tuple[str, str]:
    """Return (bucket_label, color). One ticket goes in exactly one bucket —
    most severe category wins: conflict > delayed > late > pending > ready."""
    # Scan blocked utilities for reason
    has_conflict = False
    has_delayed = False
    for s in view.utility_statuses or []:
        if s.get("status") not in (UTIL_STATUS_DELAYED, UTIL_STATUS_NOT_CLEAR):
            continue
        reason = _blocked_reason(s.get("last_response"))
        if reason in ("in conflict", "cannot locate", "no response"):
            has_conflict = True
        elif reason == "delayed":
            has_delayed = True

    if has_conflict:
        return ("Blocked — conflict/other", simplekml.Color.darkred)
    if has_delayed:
        return ("Blocked — delayed", simplekml.Color.orangered)
    if view.has_late_utility:
        return ("Has late utilities", simplekml.Color.orange)
    if view.is_ready_to_dig:
        return ("Ready to dig", simplekml.Color.green)
    # Everything else — pending, or no utility data
    return ("Pending utilities", simplekml.Color.yellow)


def is_problem_ticket(view: TicketView) -> bool:
    """Warning-icon predicate: late OR any blocking code regardless of reason."""
    return view.has_late_utility or bool(view.blocking_utility_codes)


# ── Style cache ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _StyleKey:
    color: str
    icon_url: str | None  # None → default pushpin


class _StyleCache:
    def __init__(self, kml: simplekml.Kml):
        self._kml = kml
        self._cache: dict[_StyleKey, simplekml.Style] = {}
        self._circle_cache: dict[str, simplekml.Style] = {}

    def get(self, color: str, *, caution: bool) -> simplekml.Style:
        key = _StyleKey(color=color, icon_url=CAUTION_ICON_URL if caution else None)
        if key not in self._cache:
            style = simplekml.Style()
            style.iconstyle.color = color
            if caution:
                style.iconstyle.icon.href = CAUTION_ICON_URL
                style.iconstyle.scale = 1.2
            style.linestyle.color = color
            style.linestyle.width = 4
            style.labelstyle.scale = 0.8
            self._cache[key] = style
        return self._cache[key]

    def circle(self, color: str) -> simplekml.Style:
        """Return a polygon style with fill/outline alpha-faded from `color`."""
        if color not in self._circle_cache:
            s = simplekml.Style()
            s.polystyle.color = _apply_alpha(color, CIRCLE_FILL_ALPHA)
            s.polystyle.fill = 1
            s.polystyle.outline = 1
            s.linestyle.color = _apply_alpha(color, CIRCLE_OUTLINE_ALPHA)
            s.linestyle.width = 1
            self._circle_cache[color] = s
        return self._circle_cache[color]


# ── Top-level entry points ────────────────────────────────────────────────────


@dataclass
class KmzManifest:
    out_path: Path
    generated_at: datetime
    total_placemarks: int
    folder_counts: dict[str, int]
    skipped_no_coords: int
    active_relevant: int
    cancelled_relevant_14d: int
    contractor_other: int
    dig_zone_circles: int = 0
    nashdigs_active: int = 0
    nashdigs_committed: int = 0
    nashdigs_recent: int = 0
    conflict_features: int = 0


# NashDigs rendering palette (fill alpha applied via _apply_alpha)
_NASHDIGS_ACTIVE_COLOR = simplekml.Color.green         # Status='Started'
_NASHDIGS_COMMITTED_COLOR = simplekml.Color.blue        # Status='Committed'
_NASHDIGS_RECENT_COLOR = simplekml.Color.gray           # ActEnd within 30d
_CONFLICT_FILL_ALPHA = 0.10
_CONFLICT_OUTLINE_ALPHA = 0.40
_NASHDIGS_LINE_WIDTH = 3


def export(
    session: Session,
    out_path: Path | str = DEFAULT_OUT_PATH,
    now: datetime | None = None,
    desktop_mirror_path: Path | str | None = None,
) -> KmzManifest:
    """Query the DB, assemble the KMZ, and optionally mirror it to a desktop dir.

    The mirror path is treated as a directory; the .kmz file is copied into it.
    Errors on the mirror side log a warning and do not fail the primary write.
    """
    now = now or datetime.now(timezone.utc)
    now_ct = now.astimezone(CT)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    views_relevant, views_contractor_other = _load_views(session, now_ct)
    nashdigs_target, nashdigs_others = _load_nashdigs(session)
    package_ticket_counts = _load_ticket_package_counts(session)

    manifest = _build_and_save(
        views_relevant=views_relevant,
        views_contractor_other=views_contractor_other,
        out_path=out_path,
        now_ct=now_ct,
        nashdigs_target=nashdigs_target,
        nashdigs_others=nashdigs_others,
        package_ticket_counts=package_ticket_counts,
    )

    if desktop_mirror_path:
        _mirror_kmz(out_path, Path(desktop_mirror_path))

    return manifest


def _load_nashdigs(
    session: Session,
) -> tuple[list[NashDigsPackage], list[NashDigsPackage]]:
    """Return (target_packages, non_target_packages) from the cache.

    Non-Northstar packages seed the optional Conflict Awareness layer. Empty
    list when the user hasn't run `fetch-nashdigs --all-owners`.
    """
    all_cached = load_cached(session)
    northstar: list[NashDigsPackage] = []
    others: list[NashDigsPackage] = []
    for p in all_cached:
        if p.owner and "NORTHSTAR" in p.owner.upper():
            northstar.append(p)
        else:
            others.append(p)
    return northstar, others


def _load_ticket_package_counts(
    session: Session,
) -> dict[str, dict[str, int]]:
    """Aggregate counts of relevant tickets per NashDigs project_name.

    Returns: {project_name: {'total': N, 'high': n, 'medium': n, 'low': n, ...}}
    """
    from collections import defaultdict

    rows = (
        session.query(
            ORMTicket.nashdigs_project_name,
            ORMTicket.nashdigs_match_confidence,
            ORMTicket.is_cancelled,
        )
        .filter(
            ORMTicket.is_relevant == True,  # noqa: E712
            ORMTicket.nashdigs_project_name.isnot(None),
        )
        .all()
    )
    counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "active": 0, "cancelled": 0,
                 "high": 0, "medium": 0, "low": 0}
    )
    for project_name, confidence, cancelled in rows:
        c = counts[project_name]
        c["total"] += 1
        if cancelled:
            c["cancelled"] += 1
        else:
            c["active"] += 1
        if confidence in ("high", "medium", "low"):
            c[confidence] += 1
    return counts


def _load_views(
    session: Session, now_ct: datetime
) -> tuple[list[TicketView], list[TicketView]]:
    """Pull the two groups of tickets the KMZ needs: all relevant + any
    Meridian-adjacent tickets that somehow slipped classification."""
    relevant_orm = (
        session.query(ORMTicket).filter(ORMTicket.is_relevant == True).all()  # noqa: E712
    )
    relevant_views = [ticket_view_from_orm(t, now_ct) for t in relevant_orm]

    contractor_other_orm = (
        session.query(ORMTicket)
        .filter(ORMTicket.is_relevant == False)  # noqa: E712
        .filter(ORMTicket.done_for.ilike("%Meridian Cable%"))
        .all()
    )
    contractor_other_views = [ticket_view_from_orm(t, now_ct) for t in contractor_other_orm]
    return relevant_views, contractor_other_views


def _build_and_save(
    views_relevant: list[TicketView],
    views_contractor_other: list[TicketView],
    out_path: Path,
    now_ct: datetime,
    nashdigs_target: list[NashDigsPackage] | None = None,
    nashdigs_others: list[NashDigsPackage] | None = None,
    package_ticket_counts: dict[str, dict[str, int]] | None = None,
) -> KmzManifest:
    """Assemble the full KML tree and write the .kmz."""
    today = now_ct.date()
    cutoff_cancelled_14d = today - timedelta(days=14)

    active_relevant = [v for v in views_relevant if not v.is_cancelled]
    cancelled_14d = [
        v for v in views_relevant
        if v.is_cancelled and v.call_date and v.call_date >= cutoff_cancelled_14d
    ]

    skipped_no_coords = sum(
        1 for v in active_relevant + cancelled_14d + views_contractor_other
        if v.latitude is None or v.longitude is None
    )

    kml = simplekml.Kml()
    kml.document.name = (
        f"Meridian Ops Snapshot — {now_ct.strftime('%Y-%m-%d %H:%M')} "
        f"{now_ct.tzname() or 'CT'}"
    )
    kml.document.description = (
        f"Generated {now_ct.isoformat()}. "
        f"Active relevant: {len(active_relevant)}  |  "
        f"Cancelled relevant (14d): {len(cancelled_14d)}  |  "
        f"Meridian non-relevant: {len(views_contractor_other)}  |  "
        f"Skipped (no coords): {skipped_no_coords}\n\n"
        "Pins show TN811-provided coordinates. Actual dig zones are approximate — "
        "toggle 'Dig zone circles' for ±25m visual tolerance."
    )

    styles = _StyleCache(kml)
    folder_counts: dict[str, int] = {}
    total_placemarks = 0

    # ── Axis 1: urgency (visible) ────────────────────────────────────────────
    urgency_root = kml.newfolder(name="Active relevant — by urgency")
    urgency_root.visibility = 1
    urgency_buckets: dict[str, list[TicketView]] = {}
    for v in active_relevant:
        if v.latitude is None or v.longitude is None:
            continue
        label, _ = urgency_bucket(v)
        urgency_buckets.setdefault(label, []).append(v)
    for label in ("Expiring ≤ 4 days", "Expiring 5–7 days",
                  "Expiring 8+ days", "No expire date"):
        bucket = urgency_buckets.get(label, [])
        sub = urgency_root.newfolder(name=f"{label} ({len(bucket)})")
        sub.visibility = 1
        color = _urgency_color_for_label(label)
        for v in bucket:
            _add_placemark(sub, v, color, styles)
            total_placemarks += 1
        folder_counts[f"urgency/{label}"] = len(bucket)
    urgency_root.name = f"Active relevant — by urgency ({sum(len(b) for b in urgency_buckets.values())})"

    # ── Axis 2: utility status (hidden) ──────────────────────────────────────
    status_root = kml.newfolder(name="Active relevant — by utility status")
    status_root.visibility = 0
    status_buckets: dict[str, list[TicketView]] = {}
    for v in active_relevant:
        if v.latitude is None or v.longitude is None:
            continue
        label, _ = utility_status_bucket(v)
        status_buckets.setdefault(label, []).append(v)
    for label in ("Ready to dig", "Pending utilities", "Has late utilities",
                  "Blocked — delayed", "Blocked — conflict/other"):
        bucket = status_buckets.get(label, [])
        sub = status_root.newfolder(name=f"{label} ({len(bucket)})")
        sub.visibility = 0
        color = _status_color_for_label(label)
        for v in bucket:
            _add_placemark(sub, v, color, styles)
            total_placemarks += 1
        folder_counts[f"status/{label}"] = len(bucket)
    status_root.name = f"Active relevant — by utility status ({sum(len(b) for b in status_buckets.values())})"

    # ── Axis 3: by sub (hidden) ──────────────────────────────────────────────
    # Merge raw-name aliases via slug so "North Ridge" and "North Ridge Contractors"
    # share one folder — matches the CSV exporter's behavior for consistency.
    sub_root = kml.newfolder(name="Active relevant — by sub")
    sub_root.visibility = 0
    views_for_grouping = [
        v for v in active_relevant
        if v.latitude is not None and v.longitude is not None
    ]
    by_slug = _group_by_slug(views_for_grouping)
    total_sub_placed = 0
    # Sort by active count desc, then canonical display name
    sorted_slugs = sorted(
        by_slug,
        key=lambda s: (-len(by_slug[s][1]), by_slug[s][0]),
    )
    for slug in sorted_slugs:
        display_name, bucket = by_slug[slug]
        sub = sub_root.newfolder(name=f"{display_name} ({len(bucket)})")
        sub.visibility = 0
        color = sub_color(slug)  # hash on slug so aliases share a color
        for v in bucket:
            _add_placemark(sub, v, color, styles)
            total_placemarks += 1
            total_sub_placed += 1
        folder_counts[f"sub/{slug}"] = len(bucket)
    sub_root.name = f"Active relevant — by sub ({total_sub_placed})"

    # ── Dig zone circles (±25 m tolerance, hidden by default) ───────────────
    # Only point-rendered tickets get a circle; polyline tickets already convey
    # their dig area via the LineString. Grouped by urgency bucket so the color
    # still carries urgency information at a glance.
    circles_root = kml.newfolder(name="Dig zone circles (±25m approximate)")
    circles_root.visibility = 0
    circle_buckets: dict[str, list[TicketView]] = {}
    for v in active_relevant:
        if not _is_point_only(v):
            continue
        label, _ = urgency_bucket(v)
        circle_buckets.setdefault(label, []).append(v)

    circle_total = 0
    for label in ("Expiring ≤ 4 days", "Expiring 5–7 days",
                  "Expiring 8+ days", "No expire date"):
        bucket = circle_buckets.get(label, [])
        sub = circles_root.newfolder(name=f"{label} ({len(bucket)})")
        sub.visibility = 0
        color = _urgency_color_for_label(label)
        for v in bucket:
            _add_circle(sub, v, color, styles)
            circle_total += 1
        folder_counts[f"circle/{label}"] = len(bucket)
    circles_root.name = (
        f"Dig zone circles (±25m approximate) ({circle_total})"
    )

    # ── Cancelled relevant (last 14 days) ──────────────────────────────────────
    cancelled_folder = kml.newfolder(
        name=f"Cancelled relevant — last 14 days ({len(cancelled_14d)})"
    )
    cancelled_folder.visibility = 0
    for v in cancelled_14d:
        if v.latitude is None or v.longitude is None:
            continue
        _add_placemark(cancelled_folder, v, simplekml.Color.gray, styles)
        total_placemarks += 1
    folder_counts["cancelled_14d"] = len(cancelled_14d)

    # ── Meridian non-relevant (only if any) ───────────────────────────────────────
    if views_contractor_other:
        contractor_other_folder = kml.newfolder(
            name=f"Meridian non-relevant ({len(views_contractor_other)})"
        )
        contractor_other_folder.visibility = 0
        for v in views_contractor_other:
            if v.latitude is None or v.longitude is None:
                continue
            _add_placemark(contractor_other_folder, v, simplekml.Color.purple, styles)
            total_placemarks += 1
        folder_counts["contractor_other"] = len(views_contractor_other)

    # ── NashDigs Northstar packages ─────────────────────────────────────────────
    nashdigs_counts = {"active": 0, "committed": 0, "recent": 0, "conflict": 0}
    if nashdigs_target:
        nashdigs_counts.update(_add_nashdigs_target_folder(
            kml, nashdigs_target, today, package_ticket_counts or {}
        ))
        folder_counts["nashdigs_active"] = nashdigs_counts["active"]
        folder_counts["nashdigs_committed"] = nashdigs_counts["committed"]
        folder_counts["nashdigs_recent"] = nashdigs_counts["recent"]

    # ── Conflict awareness (non-Northstar projects) ─────────────────────────────
    if nashdigs_others:
        count = _add_conflict_awareness_folder(kml, nashdigs_others)
        nashdigs_counts["conflict"] = count
        folder_counts["conflict_awareness"] = count

    kml.savekmz(str(out_path))

    return KmzManifest(
        out_path=out_path,
        generated_at=now_ct,
        total_placemarks=total_placemarks,
        folder_counts=folder_counts,
        skipped_no_coords=skipped_no_coords,
        active_relevant=len(active_relevant),
        cancelled_relevant_14d=len(cancelled_14d),
        contractor_other=len(views_contractor_other),
        dig_zone_circles=circle_total,
        nashdigs_active=nashdigs_counts.get("active", 0),
        nashdigs_committed=nashdigs_counts.get("committed", 0),
        nashdigs_recent=nashdigs_counts.get("recent", 0),
        conflict_features=nashdigs_counts.get("conflict", 0),
    )


def _urgency_color_for_label(label: str) -> str:
    return {
        "Expiring ≤ 4 days": simplekml.Color.red,
        "Expiring 5–7 days": simplekml.Color.orange,
        "Expiring 8+ days": simplekml.Color.blue,
        "No expire date": simplekml.Color.gray,
    }[label]


def _status_color_for_label(label: str) -> str:
    return {
        "Ready to dig": simplekml.Color.green,
        "Pending utilities": simplekml.Color.yellow,
        "Has late utilities": simplekml.Color.orange,
        "Blocked — delayed": simplekml.Color.orangered,
        "Blocked — conflict/other": simplekml.Color.darkred,
    }[label]


def _add_placemark(
    folder: simplekml.Folder,
    view: TicketView,
    color: str,
    styles: _StyleCache,
) -> None:
    """Emit one placemark (or pin + line pair) for a ticket into the folder.

    KML coordinate order is (longitude, latitude) — reverse of DB storage.
    """
    lat, lon = view.latitude, view.longitude  # guaranteed non-None by caller
    caution = is_problem_ticket(view)
    style = styles.get(color, caution=caution)
    description = _render_popup(view)

    # Decide geometry: LineString+midpoint if >50m apart, else single Point
    if (
        view.secondary_latitude is not None
        and view.secondary_longitude is not None
        and haversine_m(lat, lon, view.secondary_latitude, view.secondary_longitude)
        > HAVERSINE_THRESHOLD_M
    ):
        slat, slon = view.secondary_latitude, view.secondary_longitude
        # LineString (the dig path). No label — pin carries the click target.
        line = folder.newlinestring(
            name="",
            coords=[(lon, lat), (slon, slat)],
        )
        line.style = style
        line.description = description  # users can click the line too
        # Midpoint pin (click target)
        mlat, mlon = _midpoint(lat, lon, slat, slon)
        pin = folder.newpoint(
            name=view.ticket_number,
            coords=[(mlon, mlat)],
            description=description,
        )
        pin.style = style
    else:
        pin = folder.newpoint(
            name=view.ticket_number,
            coords=[(lon, lat)],
            description=description,
        )
        pin.style = style


def _add_circle(
    folder: simplekml.Folder,
    view: TicketView,
    base_color: str,
    styles: _StyleCache,
) -> None:
    """Emit a 25 m tolerance ring around a point-rendered ticket."""
    ring = circle_polygon(view.latitude, view.longitude)  # already (lon, lat)
    poly = folder.newpolygon(
        name="",  # pin carries the label; circle stays unlabeled
        outerboundaryis=ring,
        description=_render_popup(view),
    )
    poly.style = styles.circle(base_color)


# ── NashDigs rendering ────────────────────────────────────────────────────────


def _add_nashdigs_target_folder(
    kml: simplekml.Kml,
    packages: list[NashDigsPackage],
    today: date,
    ticket_counts: dict[str, dict[str, int]],
) -> dict[str, int]:
    """Render Northstar-owned NashDigs packages as three sub-folders by status."""
    parent = kml.newfolder(name="NashDigs Packages (Northstar-owned)")
    parent.visibility = 0

    recent_cutoff = today - timedelta(days=30)

    active_pkgs: list[NashDigsPackage] = []
    committed_pkgs: list[NashDigsPackage] = []
    recent_pkgs: list[NashDigsPackage] = []

    for p in packages:
        # "Recently completed" takes priority over status: a package whose
        # ActEnd is in the last 30 days is worth showing even if Status still
        # says 'Started' (feeds lag reality).
        if p.act_end_date and p.act_end_date >= recent_cutoff and p.act_end_date <= today:
            recent_pkgs.append(p)
        elif (p.status or "").lower() == "started":
            active_pkgs.append(p)
        elif (p.status or "").lower() == "committed":
            committed_pkgs.append(p)
        else:
            # Anything else (rare) — tuck under the Active bucket
            active_pkgs.append(p)

    active_folder = parent.newfolder(name=f"Active packages ({len(active_pkgs)})")
    active_folder.visibility = 0
    for p in active_pkgs:
        _add_nashdigs_package(active_folder, p, _NASHDIGS_ACTIVE_COLOR, ticket_counts)

    committed_folder = parent.newfolder(
        name=f"Committed — not yet started ({len(committed_pkgs)})"
    )
    committed_folder.visibility = 0
    for p in committed_pkgs:
        _add_nashdigs_package(committed_folder, p, _NASHDIGS_COMMITTED_COLOR, ticket_counts)

    recent_folder = parent.newfolder(
        name=f"Recently completed — last 30 days ({len(recent_pkgs)})"
    )
    recent_folder.visibility = 0
    for p in recent_pkgs:
        _add_nashdigs_package(recent_folder, p, _NASHDIGS_RECENT_COLOR, ticket_counts)

    parent.name = (
        f"NashDigs Packages (Northstar-owned) ({len(active_pkgs) + len(committed_pkgs) + len(recent_pkgs)})"
    )
    return {
        "active": len(active_pkgs),
        "committed": len(committed_pkgs),
        "recent": len(recent_pkgs),
    }


def _add_nashdigs_package(
    folder: simplekml.Folder,
    pkg: NashDigsPackage,
    base_color: str,
    ticket_counts: dict[str, dict[str, int]],
) -> None:
    """Add a single NashDigs package's geometry + popup to the given folder."""
    geom = pkg.geometry_geojson or {}
    gtype = geom.get("type")
    coords = geom.get("coordinates") or []
    if not gtype or not coords:
        return

    description = _render_package_popup(pkg, ticket_counts.get(pkg.project_name, {}))

    if gtype == "MultiLineString":
        placemark = folder.newmultigeometry(name=pkg.project_name)
        for segment in coords:
            if len(segment) < 2:
                continue
            placemark.newlinestring(
                coords=[(pt[0], pt[1]) for pt in segment],
            )
        placemark.description = description
    elif gtype == "LineString":
        placemark = folder.newlinestring(
            name=pkg.project_name,
            coords=[(pt[0], pt[1]) for pt in coords],
            description=description,
        )
    elif gtype == "Polygon":
        # outerboundary = first ring
        if not coords or not coords[0]:
            return
        placemark = folder.newpolygon(
            name=pkg.project_name,
            outerboundaryis=[(pt[0], pt[1]) for pt in coords[0]],
            description=description,
        )
    elif gtype == "MultiPolygon":
        placemark = folder.newmultigeometry(name=pkg.project_name)
        for polygon in coords:
            if not polygon or not polygon[0]:
                continue
            placemark.newpolygon(
                outerboundaryis=[(pt[0], pt[1]) for pt in polygon[0]],
            )
        placemark.description = description
    else:
        return

    # Style inline — NashDigs styles are distinct from ticket pin styles
    style = simplekml.Style()
    style.linestyle.color = base_color
    style.linestyle.width = _NASHDIGS_LINE_WIDTH
    style.polystyle.color = _apply_alpha(base_color, 0.25)
    style.polystyle.fill = 1
    style.polystyle.outline = 1
    style.labelstyle.scale = 0.9
    placemark.style = style


def _add_conflict_awareness_folder(
    kml: simplekml.Kml,
    packages: list[NashDigsPackage],
) -> int:
    """Render non-Northstar NashDigs features as a subtle gray coordination layer."""
    parent = kml.newfolder(
        name=f"Conflict awareness (non-Northstar projects) ({len(packages)})"
    )
    parent.visibility = 0

    rendered = 0
    for p in packages:
        geom = p.geometry_geojson or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates") or []
        if not gtype or not coords:
            continue

        description = _render_conflict_popup(p)
        style = simplekml.Style()
        gray = simplekml.Color.gray
        style.linestyle.color = _apply_alpha(gray, _CONFLICT_OUTLINE_ALPHA)
        style.linestyle.width = 2
        style.polystyle.color = _apply_alpha(gray, _CONFLICT_FILL_ALPHA)
        style.polystyle.fill = 1
        style.polystyle.outline = 1
        style.labelstyle.scale = 0.0  # labels would clutter at this density

        name = (p.project_name or f"Project {p.project_id}")[:60]

        if gtype == "MultiLineString":
            pm = parent.newmultigeometry(name=name)
            for seg in coords:
                if len(seg) < 2:
                    continue
                pm.newlinestring(coords=[(pt[0], pt[1]) for pt in seg])
            pm.description = description
            pm.style = style
            rendered += 1
        elif gtype == "LineString":
            pm = parent.newlinestring(
                name=name,
                coords=[(pt[0], pt[1]) for pt in coords],
                description=description,
            )
            pm.style = style
            rendered += 1
        elif gtype == "Polygon":
            if coords and coords[0]:
                pm = parent.newpolygon(
                    name=name,
                    outerboundaryis=[(pt[0], pt[1]) for pt in coords[0]],
                    description=description,
                )
                pm.style = style
                rendered += 1
        elif gtype == "MultiPolygon":
            pm = parent.newmultigeometry(name=name)
            any_ring = False
            for poly in coords:
                if poly and poly[0]:
                    pm.newpolygon(
                        outerboundaryis=[(pt[0], pt[1]) for pt in poly[0]],
                    )
                    any_ring = True
            if any_ring:
                pm.description = description
                pm.style = style
                rendered += 1
    return rendered


def _render_package_popup(
    pkg: NashDigsPackage,
    ticket_counts: dict[str, int],
) -> str:
    """HTML popup for a Northstar-owned NashDigs package."""
    import html as _html
    rows = [
        ("Package", pkg.project_name),
        ("Owner", pkg.owner),
        ("Status", pkg.status or ""),
        ("Facility", pkg.facility_type or ""),
        ("Project Type", pkg.project_type or ""),
        ("Est Start", pkg.est_start_date.isoformat() if pkg.est_start_date else ""),
        ("Est End", pkg.est_end_date.isoformat() if pkg.est_end_date else ""),
        ("Act Start", pkg.act_start_date.isoformat() if pkg.act_start_date else ""),
        ("Act End", pkg.act_end_date.isoformat() if pkg.act_end_date else ""),
        ("Council District", pkg.council_district or ""),
    ]
    trs = "".join(
        f"<tr><td><b>{_html.escape(str(k))}:</b></td><td>{_html.escape(str(v))}</td></tr>"
        for k, v in rows
    )
    ticket_block = ""
    if ticket_counts:
        tc = ticket_counts
        ticket_block = (
            f"<h4>Meridian relevant tickets tagged to this package</h4>"
            f"<table><tr><td><b>Total:</b></td><td>{tc.get('total', 0)}</td></tr>"
            f"<tr><td><b>Active:</b></td><td>{tc.get('active', 0)}</td></tr>"
            f"<tr><td><b>High confidence:</b></td><td>{tc.get('high', 0)}</td></tr>"
            f"<tr><td><b>Medium:</b></td><td>{tc.get('medium', 0)}</td></tr>"
            f"<tr><td><b>Low (review):</b></td><td>{tc.get('low', 0)}</td></tr>"
            f"</table>"
        )
    desc_block = ""
    if pkg.description:
        desc_block = f"<h4>Description</h4><p>{_html.escape(pkg.description)}</p>"
    return f"<h3>{_html.escape(pkg.project_name)}</h3><table>{trs}</table>{ticket_block}{desc_block}"


def _render_conflict_popup(pkg: NashDigsPackage) -> str:
    """Minimal popup for non-Northstar NashDigs features."""
    import html as _html
    rows = [
        ("Project", pkg.project_name),
        ("Owner", pkg.owner),
        ("Department", pkg.department or ""),
        ("Facility", pkg.facility_type or ""),
        ("Type", pkg.project_type or ""),
        ("Status", pkg.status or ""),
        ("Act Start", pkg.act_start_date.isoformat() if pkg.act_start_date else ""),
        ("Act End", pkg.act_end_date.isoformat() if pkg.act_end_date else ""),
    ]
    trs = "".join(
        f"<tr><td><b>{_html.escape(str(k))}:</b></td><td>{_html.escape(str(v))}</td></tr>"
        for k, v in rows
    )
    return f"<h3>{_html.escape(pkg.project_name or '(unnamed)')}</h3><table>{trs}</table>"


# ── Desktop mirror ────────────────────────────────────────────────────────────


def _mirror_kmz(src_file: Path, dest_dir: Path) -> bool:
    """Copy the single .kmz into the desktop mirror folder. Replaces any prior
    file of the same name; leaves unrelated files alone.

    Matches the spirit of `mirror_to_desktop` in csv_export.py — errors log
    WARNING and never raise. Primary write is the source of truth.
    """
    import shutil

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(
            "KMZ desktop mirror: cannot create destination — skipping",
            extra={"dest": str(dest_dir), "error": str(exc)},
        )
        return False

    dest_file = dest_dir / src_file.name
    try:
        if dest_file.exists():
            dest_file.unlink()
        shutil.copy2(src_file, dest_file)
    except OSError as exc:
        logger.warning(
            "KMZ desktop mirror: copy failed — primary .kmz is intact",
            extra={"src": str(src_file), "dest": str(dest_file), "error": str(exc)},
        )
        return False

    logger.info(
        "KMZ desktop mirror: bundle copied",
        extra={"src": str(src_file), "dest": str(dest_file)},
    )
    return True
