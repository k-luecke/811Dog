"""
connectors/nashdigs_match.py — spatial join tickets → NashDigs packages.

Every relevant-related ticket with lat/lon is tested against the cached
Northstar-owned NashDigs polylines. The nearest polyline (measured in meters,
after reprojecting to UTM Zone 16N) determines the ticket's package_id and
a confidence tier:

  high    ≤  25 m   direct hit; assigned with confidence
  medium  25–75 m   assigned; likely correct given portal coord tolerance
  low     75–150 m  NOT auto-assigned — candidate for manual review
  none   >150 m     no assignment; no plausible package

The tier thresholds bake in the portal's 20–50 m coord offset we measured
earlier. CRS reprojection uses pyproj with WGS84 (EPSG:4326) → UTM 16N
(EPSG:26916, meters). UTM 16N covers the entire Davidson/Rutherford area
with < 0.04% scale distortion — plenty accurate at the 25 m decision scale.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

import pyproj
from shapely.geometry import MultiLineString, Point, shape
from shapely.ops import transform
from shapely.strtree import STRtree
from sqlalchemy.orm import Session

from tn811.connectors.nashdigs import NashDigsPackage, load_cached
from tn811.models import ORMTicket

logger = logging.getLogger(__name__)

# Confidence tier thresholds, in meters
TIER_HIGH_MAX_M = 25.0
TIER_MEDIUM_MAX_M = 75.0
TIER_LOW_MAX_M = 150.0

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCE_NONE = "none"

# EPSG:26916 = UTM Zone 16N, NAD83, units meters. Covers all of middle TN.
_UTM_16N = "EPSG:26916"
_WGS84 = "EPSG:4326"
_TO_UTM = pyproj.Transformer.from_crs(_WGS84, _UTM_16N, always_xy=True).transform


@dataclass
class MatchStats:
    total: int = 0
    skipped_no_coords: int = 0
    skipped_no_packages: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    none: int = 0


def classify_distance(distance_m: float) -> str:
    """Map raw meters distance to the confidence-tier label."""
    if distance_m <= TIER_HIGH_MAX_M:
        return CONFIDENCE_HIGH
    if distance_m <= TIER_MEDIUM_MAX_M:
        return CONFIDENCE_MEDIUM
    if distance_m <= TIER_LOW_MAX_M:
        return CONFIDENCE_LOW
    return CONFIDENCE_NONE


def match_all_relevant_tickets(
    session: Session,
    *,
    dry_run: bool = False,
) -> MatchStats:
    """Reproject cached packages, walk every relevant ticket, assign nearest.

    Writes `nashdigs_project_name`, `nashdigs_match_confidence`, and
    `nashdigs_distance_m` to each ORMTicket row unless dry_run=True.
    """
    packages = load_cached(session)
    stats = MatchStats()

    if not packages:
        logger.warning(
            "No NashDigs packages cached — run fetch-nashdigs first. "
            "No ticket assignments will change."
        )
        # Walk tickets only to get a skip count
        tickets = (
            session.query(ORMTicket)
            .filter(ORMTicket.is_relevant == True)  # noqa: E712
            .all()
        )
        stats.total = len(tickets)
        stats.skipped_no_packages = len(tickets)
        return stats

    package_geoms_utm = []
    package_indices: list[int] = []  # parallel index into `packages`
    for i, p in enumerate(packages):
        try:
            geom_wgs = shape(p.geometry_geojson)
            geom_utm = transform(_TO_UTM, geom_wgs)
        except Exception as exc:
            logger.warning(
                "Skipping NashDigs package with unparseable geometry",
                extra={"project_name": p.project_name, "error": str(exc)},
            )
            continue
        package_geoms_utm.append(geom_utm)
        package_indices.append(i)

    if not package_geoms_utm:
        logger.warning("No NashDigs package geometries parsed — aborting match.")
        stats.skipped_no_packages = 1
        return stats

    tree = STRtree(package_geoms_utm)

    tickets = (
        session.query(ORMTicket)
        .filter(ORMTicket.is_relevant == True)  # noqa: E712
        .all()
    )

    for t in tickets:
        stats.total += 1
        if t.latitude is None or t.longitude is None:
            stats.skipped_no_coords += 1
            if not dry_run:
                t.nashdigs_project_name = None
                t.nashdigs_match_confidence = CONFIDENCE_NONE
                t.nashdigs_distance_m = None
            continue

        pt_wgs = Point(t.longitude, t.latitude)  # shapely order: (x=lon, y=lat)
        pt_utm = transform(_TO_UTM, pt_wgs)

        nearest_local_ix = tree.nearest(pt_utm)
        nearest_geom = package_geoms_utm[nearest_local_ix]
        distance_m = pt_utm.distance(nearest_geom)
        tier = classify_distance(distance_m)

        match_pkg = packages[package_indices[nearest_local_ix]]

        if tier == CONFIDENCE_HIGH:
            stats.high += 1
        elif tier == CONFIDENCE_MEDIUM:
            stats.medium += 1
        elif tier == CONFIDENCE_LOW:
            stats.low += 1
        else:
            stats.none += 1

        if dry_run:
            continue

        # Auto-assign only for high + medium; low is stored (with the
        # candidate name) but exported to the review CSV so the supervisor
        # can confirm. `none` gets no package_name assigned.
        if tier in (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW):
            t.nashdigs_project_name = match_pkg.project_name
        else:
            t.nashdigs_project_name = None
        t.nashdigs_match_confidence = tier
        t.nashdigs_distance_m = round(distance_m, 2)

    return stats
