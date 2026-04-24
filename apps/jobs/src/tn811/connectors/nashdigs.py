"""
connectors/nashdigs.py — pull Google-owned fiber projects from NashDigs.

NashDigs (Metro Nashville's public project-coordination FeatureServer) hosts
every active and committed GFiber package as a MultiLineString on Layer 1
with ProjectName = the BNA### package identifier Google (and Ervin) already
use internally. This connector:

  1. Queries the public FeatureServer for all `Owner = 'Google'` line features
     in WGS84 (outSR=4326) GeoJSON.
  2. Parses them into NashDigsPackage dataclasses with full geometry.
  3. Upserts into the local ORMNashDigsProject table (keyed on ProjectID),
     deleting records that no longer appear in NashDigs (e.g., projects that
     were cancelled or left Google ownership).

Public service — no auth. No MPK download, no tenant extraction. Safe to run
at every scrape.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import httpx

from tn811.models import ORMNashDigsProject

logger = logging.getLogger(__name__)

# ── Endpoint + canonical filter ───────────────────────────────────────────────

SERVICE_BASE = (
    "https://services2.arcgis.com/HdTo6HJqh92wn4D8/ArcGIS/rest/services/"
    "NashDigs_Project_Layers_view/FeatureServer"
)
LINES_QUERY_URL = f"{SERVICE_BASE}/1/query"

# Owner filter — safety check confirmed Owner='Google' == Department='Google'
# on 67/67 features; UPPER(...)LIKE handles any future casing drift.
DEFAULT_WHERE = "UPPER(Owner) LIKE '%GOOGLE%'"
DEFAULT_USER_AGENT = "811dog-nashdigs/0.1"


# ── Data class ────────────────────────────────────────────────────────────────


@dataclass
class NashDigsPackage:
    """A single Google-owned fiber project pulled from NashDigs."""

    project_id: int
    project_name: str                 # BNA### — the package identifier
    facility_type: str | None         # "Communication"
    project_type: str | None          # "Underground"
    description: str | None
    owner: str                        # "Google"
    department: str | None
    division: str | None
    status: str | None                # "Started" | "Committed" | …
    council_district: str | None
    est_start_date: date | None
    est_end_date: date | None
    act_start_date: date | None
    act_end_date: date | None
    geometry_geojson: dict[str, Any]  # GeoJSON MultiLineString in WGS84


# ── Public API ────────────────────────────────────────────────────────────────


def fetch_google_packages(
    client: httpx.Client | None = None,
    where: str = DEFAULT_WHERE,
) -> list[NashDigsPackage]:
    """Query NashDigs Layer 1 (Lines) for Google-owned packages.

    Returns all matching features in a single request. The current dataset
    (67 features) fits in one response; if NashDigs ever adds pagination for
    Google-owned work, the service would return `exceededTransferLimit: true`
    and we'd need to page via objectIds or resultOffset. For now, one call.
    """
    close_client = False
    if client is None:
        client = httpx.Client(
            timeout=30.0,
            headers={"User-Agent": DEFAULT_USER_AGENT},
            follow_redirects=True,
        )
        close_client = True

    try:
        params = {
            "where": where,
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": 4326,  # WGS84; match ORMTicket.latitude/longitude CRS
            "f": "geojson",
        }
        logger.info(
            "Fetching NashDigs Google packages",
            extra={"url": LINES_QUERY_URL, "where": where},
        )
        r = client.get(LINES_QUERY_URL, params=params)
        r.raise_for_status()
        data = r.json()
    finally:
        if close_client:
            client.close()

    if data.get("exceededTransferLimit"):
        logger.warning(
            "NashDigs response hit transfer limit — pagination may be needed"
        )

    features = data.get("features", [])
    packages = [_parse_feature(f) for f in features]
    logger.info(
        "Parsed NashDigs packages",
        extra={"count": len(packages)},
    )
    return packages


def upsert_packages(
    session,
    packages: list[NashDigsPackage],
    now: datetime,
) -> dict[str, int]:
    """Sync the list of packages to the DB. Keyed on project_id.

    Replace-all semantics for the Google-owned projection: records currently
    in the DB that are not in the new list are deleted (Google left the
    project, or it was cancelled). New records inserted; changed records
    updated; unchanged records skipped.
    """
    existing_rows = {
        row.project_id: row
        for row in session.query(ORMNashDigsProject).all()
    }
    incoming_ids = {p.project_id for p in packages}

    stats = {"new": 0, "updated": 0, "unchanged": 0, "deleted": 0}

    for p in packages:
        dict_payload = _to_orm_dict(p, now)
        existing = existing_rows.get(p.project_id)
        if existing is None:
            session.add(ORMNashDigsProject(**dict_payload, created_at=now))
            stats["new"] += 1
        else:
            # Compare a stable subset to detect change
            changed = False
            for key, val in dict_payload.items():
                if key == "fetched_at":
                    continue
                if getattr(existing, key) != val:
                    changed = True
                    break
            if changed:
                for k, v in dict_payload.items():
                    setattr(existing, k, v)
                stats["updated"] += 1
            else:
                existing.fetched_at = now
                stats["unchanged"] += 1

    # Delete stale
    for pid, row in existing_rows.items():
        if pid not in incoming_ids:
            session.delete(row)
            stats["deleted"] += 1

    return stats


def load_cached(session) -> list[NashDigsPackage]:
    """Read cached packages back out of the DB (for the spatial-match step)."""
    rows = session.query(ORMNashDigsProject).all()
    return [_from_orm(row) for row in rows]


# ── Private helpers ───────────────────────────────────────────────────────────


def _parse_feature(feature: dict[str, Any]) -> NashDigsPackage:
    """Convert a single GeoJSON feature from NashDigs to a NashDigsPackage."""
    props = feature.get("properties") or {}
    geom = feature.get("geometry") or {}

    return NashDigsPackage(
        project_id=int(props.get("ProjectID")),
        project_name=str(props.get("ProjectName") or "").strip(),
        facility_type=_stripped_or_none(props.get("FacilityType")),
        project_type=_stripped_or_none(props.get("ProjectType")),
        description=_stripped_or_none(props.get("Description")),
        owner=str(props.get("Owner") or "").strip(),
        department=_stripped_or_none(props.get("Department")),
        division=_stripped_or_none(props.get("Division")),
        status=_stripped_or_none(props.get("Status")),
        council_district=_stripped_or_none(props.get("CouncilDistrict")),
        est_start_date=_date_from_ms_epoch(props.get("EstStartDate")),
        est_end_date=_date_from_ms_epoch(props.get("EstEndDate")),
        act_start_date=_date_from_ms_epoch(props.get("ActStartDate")),
        act_end_date=_date_from_ms_epoch(props.get("ActEndDate")),
        geometry_geojson=geom,
    )


def _stripped_or_none(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _date_from_ms_epoch(val: Any) -> date | None:
    """NashDigs emits *Date fields as milliseconds-since-epoch integers."""
    if val is None or val == "":
        return None
    try:
        return datetime.fromtimestamp(int(val) / 1000, tz=timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def _to_orm_dict(p: NashDigsPackage, now: datetime) -> dict[str, Any]:
    return {
        "project_id": p.project_id,
        "project_name": p.project_name,
        "facility_type": p.facility_type,
        "project_type": p.project_type,
        "description": p.description,
        "owner": p.owner,
        "department": p.department,
        "division": p.division,
        "status": p.status,
        "council_district": p.council_district,
        "est_start_date": p.est_start_date.isoformat() if p.est_start_date else None,
        "est_end_date": p.est_end_date.isoformat() if p.est_end_date else None,
        "act_start_date": p.act_start_date.isoformat() if p.act_start_date else None,
        "act_end_date": p.act_end_date.isoformat() if p.act_end_date else None,
        "geometry_geojson": p.geometry_geojson,
        "fetched_at": now,
    }


def _from_orm(row: ORMNashDigsProject) -> NashDigsPackage:
    return NashDigsPackage(
        project_id=row.project_id,
        project_name=row.project_name,
        facility_type=row.facility_type,
        project_type=row.project_type,
        description=row.description,
        owner=row.owner,
        department=row.department,
        division=row.division,
        status=row.status,
        council_district=row.council_district,
        est_start_date=date.fromisoformat(row.est_start_date) if row.est_start_date else None,
        est_end_date=date.fromisoformat(row.est_end_date) if row.est_end_date else None,
        act_start_date=date.fromisoformat(row.act_start_date) if row.act_start_date else None,
        act_end_date=date.fromisoformat(row.act_end_date) if row.act_end_date else None,
        geometry_geojson=row.geometry_geojson or {},
    )
