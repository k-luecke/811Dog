"""
cli.py — Click CLI entry point for TN811 Monitor backend jobs.

Commands:
  scrape       Run the portal scrape pipeline
  remind       Send expiry reminder emails
  build-json   Rebuild dashboard JSON exports
  init-db      Initialize the database schema
  show-config  Print parsed config
  list-tickets List tickets from the database
  reset-reminders  Clear reminder event history (danger)
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

logger = logging.getLogger(__name__)


def _load_app(config_path: str):
    """Load config, init logging, init DB."""
    from tn811.config import load_config
    from tn811.logging import configure_logging

    cfg = load_config(config_path)
    configure_logging(
        level=cfg.logging.level,
        fmt=cfg.logging.format,
        file=cfg.logging.file,
    )
    cfg.paths.ensure_dirs()

    from tn811.db import init_db
    init_db(cfg)

    return cfg


@click.group()
def main():
    """TN811 Monitor — internal GFiber ticket tracking tool."""
    pass


# ── scrape ─────────────────────────────────────────────────────────────────────

@main.command()
@click.option("--config", "config_path", default="config/monitoring.yaml", show_default=True)
@click.option("--dry-run", is_flag=True, help="No DB writes; no PDF downloads")
@click.option("--county", "county_filter", default=None, help="Only scrape this county")
def scrape(config_path: str, dry_run: bool, county_filter: str | None):
    """Scrape TN811 portal for new and changed tickets."""
    cfg = _load_app(config_path)

    counties = cfg.active_counties
    if county_filter:
        counties = [c for c in counties if county_filter.lower() in c.name.lower()]
        if not counties:
            click.echo(f"No matching county: {county_filter}", err=True)
            sys.exit(1)

    click.echo(f"Scraping {len(counties)} county(ies)... {'[DRY RUN]' if dry_run else ''}")

    asyncio.run(_run_scrape(cfg, counties, dry_run))


async def _run_scrape(cfg, counties, dry_run: bool):
    from datetime import timedelta

    from tn811.db import get_session
    from tn811.models import ORMScrapeRun, ScrapeRunStatus
    from tn811.portal.browser import browser_context
    from tn811.portal.detail import DetailAdapter
    from tn811.portal.search import SearchAdapter
    from tn811.relevance.matcher import RelevanceMatcher

    now = datetime.now(timezone.utc)
    date_to = now.date()
    date_from = date_to - timedelta(days=cfg.portal.date_window_days)

    matcher = RelevanceMatcher(cfg.relevance)
    search_adapter = SearchAdapter(cfg.portal, cfg.paths.raw_html_dir)
    detail_adapter = DetailAdapter(cfg.portal, cfg.paths.raw_html_dir)

    # Record scrape run
    run_id = None
    if not dry_run:
        with get_session() as session:
            run = ORMScrapeRun(
                started_at=now,
                status=ScrapeRunStatus.STARTED.value,
                counties=[c.name for c in counties],
                dry_run=dry_run,
            )
            session.add(run)
            session.flush()
            run_id = run.id

    stats = {"found": 0, "new": 0, "changed": 0, "unchanged": 0, "failures": 0}

    try:
        async with browser_context(cfg.portal) as ctx:
            for county in counties:
                click.echo(f"  County: {county.name}")
                async for row in search_adapter.search_county(ctx, county, date_from, date_to):
                    stats["found"] += 1
                    try:
                        await _process_ticket_row(
                            row, ctx, cfg, detail_adapter,
                            matcher, dry_run, stats, now,
                        )
                    except Exception as exc:
                        logger.error(
                            "Failed to process ticket row",
                            extra={"ticket": row.ticket_number, "error": str(exc)},
                            exc_info=exc,
                        )
                        stats["failures"] += 1
                        if not dry_run:
                            _record_failure(row.ticket_number, row.county, exc)

        # Run work-group inference on all GFiber-related active tickets
        if not dry_run:
            _run_grouping(cfg)

        # Update scrape run record
        if not dry_run and run_id is not None:
            with get_session() as session:
                run = session.get(ORMScrapeRun, run_id)
                if run:
                    run.finished_at = datetime.now(timezone.utc)
                    run.status = ScrapeRunStatus.SUCCESS.value
                    run.tickets_found = stats["found"]
                    run.tickets_new = stats["new"]
                    run.tickets_changed = stats["changed"]
                    run.tickets_unchanged = stats["unchanged"]
                    run.pdfs_downloaded = 0
                    run.parse_failures = stats["failures"]

    except Exception as exc:
        logger.error("Scrape run failed", exc_info=exc)
        if not dry_run and run_id is not None:
            with get_session() as session:
                run = session.get(ORMScrapeRun, run_id)
                if run:
                    run.finished_at = datetime.now(timezone.utc)
                    run.status = ScrapeRunStatus.FAILED.value
                    run.error_detail = str(exc)
        raise

    click.echo(
        f"\nScrape complete: {stats['found']} found, "
        f"{stats['new']} new, {stats['changed']} changed, "
        f"{stats['failures']} failures"
    )


_GFIBER_SUBCONTRACTORS: frozenset[str] = frozenset({
    "cui cable", "florida armstrong", "blue ocean", "dtob",
    "civcom", "amzung", "cattail",
})


def _worth_detail_fetch(row) -> bool:
    """Return True if this row is a plausible GFiber ticket worth fetching detail for.

    Kept in sync with the relevance rules' primary `done_for` signals. "ervin cable"
    is listed here so Ervin-subcontracted work (MAC Underground, Civcom, etc.)
    gets its detail page fetched at scrape time — without this, those rows go in
    row-only and miss the utility-status + full-text context the scorer relies on.
    """
    wdf = (row.work_done_for_raw or "").lower()
    exc = (row.excavator_name_raw or "").lower()
    wt = (row.work_type_raw or "").lower()
    return (
        "google" in wdf
        or "gfiber" in wdf
        or "ervin cable" in wdf
        or any(s in exc for s in _GFIBER_SUBCONTRACTORS)
        or any(kw in wt for kw in ("fiber optic", "ftth", "fiber instl", "fiber bury"))
    )


async def _process_ticket_row(
    row, ctx, cfg, detail_adapter, matcher, dry_run, stats, now
):
    from tn811.db import get_session
    from tn811.models import ORMTicket, ORMTicketSnapshot, normalized_to_orm_dict
    from tn811.normalize.tickets import normalize_ticket

    # Fetch detail page only for GFiber candidates — ~50-500 per county vs 11,000+
    detail = None
    if row.detail_url and _worth_detail_fetch(row):
        try:
            detail = await detail_adapter.fetch(ctx, row.ticket_number, row.county, row.detail_url)
        except Exception as exc:
            logger.warning(
                "Detail page fetch failed",
                extra={"ticket": row.ticket_number, "error": str(exc)},
            )

    # Normalize and score
    ticket = normalize_ticket(row, detail, now=now)
    matcher.apply(ticket)

    if dry_run:
        click.echo(
            f"    [DRY] {ticket.ticket_number} | score={ticket.relevance_score:.2f} "
            f"| gfiber={ticket.is_gfiber_related} | status={ticket.status.value}"
        )
        stats["new"] += 1
        return

    # Persist
    new_hash = ticket.content_hash()
    ticket_dict = normalized_to_orm_dict(ticket, now)

    with get_session() as session:
        existing = session.query(ORMTicket).filter_by(
            ticket_number=ticket.ticket_number
        ).first()

        if existing is None:
            orm_t = ORMTicket(**ticket_dict, created_at=now)
            session.add(orm_t)
            session.flush()
            snapshot = ORMTicketSnapshot(
                ticket_id=orm_t.id,
                content_hash=new_hash,
                payload=ticket.model_dump(mode="json"),
                captured_at=now,
            )
            session.add(snapshot)
            stats["new"] += 1
            logger.info("New ticket stored", extra={"ticket": ticket.ticket_number})

        elif existing.latest_content_hash != new_hash:
            for k, v in ticket_dict.items():
                setattr(existing, k, v)
            snapshot = ORMTicketSnapshot(
                ticket_id=existing.id,
                content_hash=new_hash,
                payload=ticket.model_dump(mode="json"),
                captured_at=now,
            )
            session.add(snapshot)
            stats["changed"] += 1
            logger.info("Ticket updated", extra={"ticket": ticket.ticket_number})

        else:
            stats["unchanged"] += 1


def _record_failure(ticket_number, county, exc):
    from tn811.db import get_session
    from tn811.models import ORMParseFailure, ParseFailureReason
    with get_session() as session:
        failure = ORMParseFailure(
            ticket_number=ticket_number,
            county=county,
            reason=ParseFailureReason.UNKNOWN.value,
            detail=str(exc),
            occurred_at=datetime.now(timezone.utc),
        )
        session.add(failure)


def _run_grouping(cfg):
    from tn811.db import get_session
    from tn811.grouping.infer import infer_work_groups
    from tn811.models import ORMTicket, TicketStatus, orm_to_normalized

    with get_session() as session:
        active_gfiber = (
            session.query(ORMTicket)
            .filter(
                ORMTicket.is_gfiber_related == True,  # noqa: E712
                ORMTicket.status == TicketStatus.ACTIVE.value,
            )
            .all()
        )
        normalized = [orm_to_normalized(t) for t in active_gfiber]
        infer_work_groups(normalized, cfg.grouping)

        # Write back
        for orm_t, norm_t in zip(active_gfiber, normalized):
            orm_t.probable_company = norm_t.probable_company
            orm_t.probable_work_group = norm_t.probable_work_group
            orm_t.probable_crew = norm_t.probable_crew


# ── remind ─────────────────────────────────────────────────────────────────────

@main.command()
@click.option("--config", "config_path", default="config/monitoring.yaml", show_default=True)
@click.option("--dry-run", is_flag=True, help="Write preview to disk instead of sending")
def remind(config_path: str, dry_run: bool):
    """Send expiry reminder emails for GFiber tickets expiring in lead_days days."""
    cfg = _load_app(config_path)

    from tn811.db import get_session
    from tn811.models import ORMReminderEvent, ORMTicket, TicketStatus, orm_to_normalized
    from tn811.reminders.emailer import ReminderEmailer
    from tn811.reminders.rules import filter_eligible_tickets, get_target_expiration_date

    target = get_target_expiration_date(cfg.reminders)
    click.echo(f"Reminder check for expiration date: {target} {'[DRY RUN]' if dry_run else ''}")

    with get_session() as session:
        # Load all active GFiber tickets
        active = (
            session.query(ORMTicket)
            .filter(
                ORMTicket.is_gfiber_related == True,  # noqa: E712
                ORMTicket.status == TicketStatus.ACTIVE.value,
            )
            .all()
        )
        normalized = [orm_to_normalized(t) for t in active]
        eligible = filter_eligible_tickets(normalized, target)

        if not eligible:
            click.echo("No eligible tickets for reminder today.")
            return

        # Check for already-sent reminders (deduplication)
        to_send = []
        for t in eligible:
            already_sent = (
                session.query(ORMReminderEvent)
                .filter_by(
                    ticket_number=t.ticket_number,
                    expiration_date=target.isoformat(),
                    lead_days=cfg.reminders.lead_days,
                    dry_run=dry_run,
                )
                .first()
            )
            if already_sent:
                logger.info("Reminder already sent", extra={"ticket": t.ticket_number})
            else:
                to_send.append(t)

        if not to_send:
            click.echo("All eligible tickets already reminded.")
            return

        click.echo(f"Sending reminder for {len(to_send)} ticket(s)...")

        emailer = ReminderEmailer(cfg.reminders, cfg.paths.reminder_preview_dir)
        result = emailer.send(to_send, target, dry_run=dry_run)

        if result.sent or result.dry_run:
            # Record reminder events
            now = datetime.now(timezone.utc)
            for t in to_send:
                event = ORMReminderEvent(
                    ticket_number=t.ticket_number,
                    sent_at=now,
                    recipients=cfg.reminders.recipients,
                    expiration_date=target.isoformat(),
                    dry_run=dry_run,
                    lead_days=cfg.reminders.lead_days,
                )
                # Need to get the ticket_id
                orm_t = session.query(ORMTicket).filter_by(
                    ticket_number=t.ticket_number
                ).first()
                if orm_t:
                    event.ticket_id = orm_t.id
                    session.add(event)

        if dry_run:
            click.echo(f"Dry run complete. Preview: {result.preview_path}")
        elif result.sent:
            click.echo(f"Reminder sent to: {', '.join(result.recipients)}")
        else:
            click.echo(f"Send failed: {result.reason}", err=True)


# ── build-json / export-dashboard ─────────────────────────────────────────────

def _do_build_json(config_path: str) -> None:
    cfg = _load_app(config_path)
    from tn811.db import get_session
    from tn811.snapshots.build_dashboard_json import build_all
    with get_session() as session:
        build_all(session, cfg)
    click.echo(f"Dashboard JSON written to: {cfg.paths.exports_dir}")


@main.command("build-json")
@click.option("--config", "config_path", default="config/monitoring.yaml", show_default=True)
def build_json(config_path: str):
    """Rebuild all dashboard JSON exports from the database."""
    _do_build_json(config_path)


@main.command("export-dashboard")
@click.option("--config", "config_path", default="config/monitoring.yaml", show_default=True)
def export_dashboard(config_path: str):
    """Alias for build-json. Rebuild all dashboard JSON exports from the database."""
    _do_build_json(config_path)


# ── export-csv ────────────────────────────────────────────────────────────────

@main.command("export-csv")
@click.option("--config", "config_path", default="config/monitoring.yaml", show_default=True)
@click.option(
    "--out",
    "out_dir",
    default=None,
    help="Output directory (defaults to paths.exports_dir from config).",
)
@click.option(
    "--sub-slices/--no-sub-slices",
    default=False,
    help="Also emit by_sub/<slug>/ folders per active excavator.",
)
@click.option(
    "--no-desktop",
    is_flag=True,
    help="Skip the Windows desktop mirror for this run (config default is honored otherwise).",
)
def export_csv(config_path: str, out_dir: str | None, sub_slices: bool, no_desktop: bool):
    """Write the supervisor CSV bundle (nine master files + optional per-sub slices)."""
    cfg = _load_app(config_path)

    from tn811.db import get_session
    from tn811.exporters.csv_export import export as run_export

    target = Path(out_dir) if out_dir else Path(cfg.paths.exports_dir)
    target.mkdir(parents=True, exist_ok=True)

    mirror_path: Path | None = None
    if not no_desktop and cfg.exports.desktop_mirror_enabled and cfg.exports.desktop_mirror_path:
        mirror_path = Path(cfg.exports.desktop_mirror_path)

    suffix_bits = []
    if sub_slices:
        suffix_bits.append("+ sub slices")
    if mirror_path:
        suffix_bits.append(f"→ mirror {mirror_path}")
    suffix = f" [{', '.join(suffix_bits)}]" if suffix_bits else ""
    click.echo(f"Writing CSV export to {target}{suffix}")

    with get_session() as session:
        manifest = run_export(
            session,
            target,
            sub_slices=sub_slices,
            desktop_mirror_path=mirror_path,
        )

    click.echo(f"\nWrote {len(manifest.master_files)} master file(s):")
    for f in manifest.master_files:
        click.echo(f"  {f.name:<34}  {f.rows:>7,} rows")
    if manifest.sub_slice_count:
        click.echo(f"\nPer-sub slices: {manifest.sub_slice_count} folder(s) in by_sub/")
    click.echo(f"\nManifest: {target / 'MANIFEST.txt'}")
    if mirror_path:
        click.echo(f"Desktop mirror: {mirror_path}")


# ── backfill ───────────────────────────────────────────────────────────────────

@main.command()
@click.option("--config", "config_path", default="config/monitoring.yaml", show_default=True)
@click.option(
    "--days",
    default=90,
    show_default=True,
    help=(
        "Total days of history to backfill. The portal only accepts windows of "
        "<=30 days, so this is broken into multiple chained searches automatically."
    ),
)
@click.option("--dry-run", is_flag=True, help="No DB writes; just show what would be scraped")
@click.option("--county", "county_filter", default=None, help="Only backfill this county")
def backfill(config_path: str, days: int, dry_run: bool, county_filter: str | None):
    """
    Backfill historical ticket data by chaining multiple 30-day search windows.

    Example — backfill 90 days:
        tn811 backfill --days 90

    This fires three searches per county:
        days 0–30, days 31–60, days 61–90
    Results are merged locally; duplicates are deduplicated by ticket_number.
    """
    from datetime import timedelta

    cfg = _load_app(config_path)

    counties = cfg.active_counties
    if county_filter:
        counties = [c for c in counties if county_filter.lower() in c.name.lower()]
        if not counties:
            click.echo(f"No matching county: {county_filter}", err=True)
            sys.exit(1)

    # Build chained windows: each window is <=30 days, working backwards from today
    MAX_WINDOW = 30
    now = datetime.now(timezone.utc)
    today = now.date()

    windows: list[tuple] = []
    remaining = days
    offset = 0
    while remaining > 0:
        window_size = min(remaining, MAX_WINDOW)
        window_end = today - timedelta(days=offset)
        window_start = today - timedelta(days=offset + window_size - 1)
        windows.append((window_start, window_end))
        offset += window_size
        remaining -= window_size

    click.echo(
        f"Backfilling {days} days across {len(windows)} search window(s) "
        f"for {len(counties)} county(ies)... {'[DRY RUN]' if dry_run else ''}"
    )
    for i, (ws, we) in enumerate(windows, 1):
        click.echo(f"  Window {i}/{len(windows)}: {ws} → {we}")

    asyncio.run(_run_backfill(cfg, counties, windows, dry_run, now))


async def _run_backfill(cfg, counties, windows, dry_run: bool, now):
    from tn811.portal.browser import browser_context
    from tn811.portal.detail import DetailAdapter
    from tn811.portal.search import SearchAdapter
    from tn811.relevance.matcher import RelevanceMatcher

    matcher = RelevanceMatcher(cfg.relevance)
    search_adapter = SearchAdapter(cfg.portal, cfg.paths.raw_html_dir)
    detail_adapter = DetailAdapter(cfg.portal, cfg.paths.raw_html_dir)

    total_stats = {"found": 0, "new": 0, "changed": 0, "unchanged": 0, "failures": 0}

    async with browser_context(cfg.portal) as ctx:
        for county in counties:
            for window_start, window_end in windows:
                click.echo(f"  Scraping {county.name}: {window_start} → {window_end}")
                stats = {"found": 0, "new": 0, "changed": 0, "unchanged": 0, "failures": 0}
                async for row in search_adapter.search_county(ctx, county, window_start, window_end):
                    stats["found"] += 1
                    total_stats["found"] += 1
                    try:
                        await _process_ticket_row(
                            row, ctx, cfg, detail_adapter,
                            matcher, dry_run, stats, now,
                        )
                    except Exception as exc:
                        logger.error(
                            "Backfill: failed to process ticket",
                            extra={"ticket": row.ticket_number, "error": str(exc)},
                            exc_info=exc,
                        )
                        stats["failures"] += 1
                        total_stats["failures"] += 1

                click.echo(
                    f"    → {stats['found']} found, {stats['new']} new, "
                    f"{stats['changed']} changed, {stats['failures']} failures"
                )
                for k in ("new", "changed", "unchanged"):
                    total_stats[k] += stats[k]

    click.echo(
        f"\nBackfill complete: {total_stats['found']} found, "
        f"{total_stats['new']} new, {total_stats['changed']} changed, "
        f"{total_stats['failures']} failures"
    )


# ── init-db ────────────────────────────────────────────────────────────────────

@main.command("init-db")
@click.option("--config", "config_path", default="config/monitoring.yaml", show_default=True)
def init_db_cmd(config_path: str):
    """Initialize the database schema."""
    from tn811.config import load_config
    from tn811.db import init_db

    cfg = load_config(config_path)
    init_db(cfg)
    click.echo(f"Database initialized: {cfg.db.url}")


# ── show-config ────────────────────────────────────────────────────────────────

@main.command("show-config")
@click.option("--config", "config_path", default="config/monitoring.yaml", show_default=True)
def show_config(config_path: str):
    """Print the parsed configuration."""
    from dataclasses import asdict
    from tn811.config import load_config

    cfg = load_config(config_path)

    # Redact SMTP password
    d = asdict(cfg)
    try:
        d["reminders"]["smtp"]["password"] = "***"
    except (KeyError, TypeError):
        pass

    click.echo(json.dumps(d, indent=2, default=str))


# ── list-tickets ───────────────────────────────────────────────────────────────

@main.command("list-tickets")
@click.option("--config", "config_path", default="config/monitoring.yaml", show_default=True)
@click.option("--status", default=None, help="Filter by status: active|cancelled|expired")
@click.option("--county", default=None)
@click.option("--gfiber-only", is_flag=True, default=True)
@click.option("--limit", default=50)
def list_tickets(config_path: str, status: str | None, county: str | None, gfiber_only: bool, limit: int):
    """List tickets from the database."""
    cfg = _load_app(config_path)

    from tn811.db import get_session
    from tn811.models import ORMTicket

    with get_session() as session:
        q = session.query(ORMTicket)
        if gfiber_only:
            q = q.filter(ORMTicket.is_gfiber_related == True)  # noqa: E712
        if status:
            q = q.filter(ORMTicket.status == status)
        if county:
            q = q.filter(ORMTicket.county.ilike(f"%{county}%"))
        tickets = q.order_by(ORMTicket.expiration_date).limit(limit).all()

    click.echo(f"{'TICKET':<25} {'COUNTY':<20} {'EXPIRES':<12} {'STATUS':<12} {'SCORE':<8} {'COMPANY'}")
    click.echo("-" * 100)
    for t in tickets:
        click.echo(
            f"{t.ticket_number:<25} {t.county:<20} {t.expiration_date or 'N/A':<12} "
            f"{t.status:<12} {t.relevance_score:<8.2f} {t.excavator_name or ''}"
        )


# ── reset-reminders ────────────────────────────────────────────────────────────

@main.command("reset-reminders")
@click.option("--config", "config_path", default="config/monitoring.yaml", show_default=True)
@click.option("--confirm", is_flag=True, required=True, help="Must confirm this destructive action")
def reset_reminders(config_path: str, confirm: bool):
    """Clear all reminder event history (allows re-sending)."""
    cfg = _load_app(config_path)

    from tn811.db import get_session
    from tn811.models import ORMReminderEvent

    with get_session() as session:
        count = session.query(ORMReminderEvent).count()
        session.query(ORMReminderEvent).delete()

    click.echo(f"Deleted {count} reminder event(s).")


# ── rescore ───────────────────────────────────────────────────────────────────

@main.command("rescore")
@click.option("--config", "config_path", default="config/monitoring.yaml", show_default=True)
@click.option("--dry-run", is_flag=True, help="Print change counts without writing to DB")
def rescore(config_path: str, dry_run: bool):
    """
    Re-score every ticket against the current relevance rules.

    Use this after changing relevance rules or adding a new prime/subcontractor
    signal — existing tickets are NOT automatically reclassified when config
    changes, since scoring happens at scrape time. Scores against the stored
    row fields only (no HTML fetch), so it works even for tickets that were
    filed row-only during a scrape (the pre-filter skipped detail fetch).
    """
    cfg = _load_app(config_path)

    from tn811.db import get_session
    from tn811.models import ORMTicket, orm_to_normalized
    from tn811.relevance.matcher import RelevanceMatcher

    matcher = RelevanceMatcher(cfg.relevance)

    stats = {"total": 0, "newly_gfiber": 0, "no_longer_gfiber": 0,
             "score_changed": 0, "unchanged": 0}
    newly_flagged_samples: list[str] = []

    with get_session() as session:
        tickets = session.query(ORMTicket).all()
        for t in tickets:
            stats["total"] += 1
            norm = orm_to_normalized(t)
            old_flag = bool(t.is_gfiber_related)
            old_score = float(t.relevance_score or 0.0)

            matcher.apply(norm)

            new_flag = norm.is_gfiber_related
            new_score = norm.relevance_score
            flag_flipped = new_flag != old_flag
            score_moved = abs(new_score - old_score) > 1e-6

            if flag_flipped:
                if new_flag:
                    stats["newly_gfiber"] += 1
                    if len(newly_flagged_samples) < 5:
                        newly_flagged_samples.append(
                            f"{t.ticket_number}  {t.excavator_name!r} "
                            f"done_for={t.done_for!r} score={new_score:.2f}"
                        )
                else:
                    stats["no_longer_gfiber"] += 1
                if not dry_run:
                    t.is_gfiber_related = new_flag
                    t.relevance_score = new_score
                    t.relevance_reasons = norm.relevance_reasons
            elif score_moved:
                stats["score_changed"] += 1
                if not dry_run:
                    t.relevance_score = new_score
                    t.relevance_reasons = norm.relevance_reasons
            else:
                stats["unchanged"] += 1

    click.echo(f"Rescored {stats['total']:,} ticket(s):")
    click.echo(f"  +{stats['newly_gfiber']:>5}  newly flagged as GFiber")
    click.echo(f"  -{stats['no_longer_gfiber']:>5}  no longer flagged as GFiber")
    click.echo(f"  ~{stats['score_changed']:>5}  score changed (flag same)")
    click.echo(f"  ={stats['unchanged']:>5}  unchanged")
    if newly_flagged_samples:
        click.echo("\nSample newly-flagged tickets:")
        for line in newly_flagged_samples:
            click.echo(f"  {line}")
    if dry_run:
        click.echo("\n[DRY RUN — no changes written]")


# ── reparse-details ───────────────────────────────────────────────────────────

@main.command("reparse-details")
@click.option("--config", "config_path", default="config/monitoring.yaml", show_default=True)
@click.option("--dry-run", is_flag=True, help="Parse and print results without writing to DB")
def reparse_details(config_path: str, dry_run: bool):
    """
    Re-parse saved detail HTML snapshots to populate utility status fields.

    Walks all tickets with a saved HTML snapshot, re-runs parse_detail_html(),
    and updates utility_statuses, location_text, intersection_text,
    legal_start_date, and all derived rollup fields in place.
    Does NOT re-scrape the portal.
    """
    from pathlib import Path

    cfg = _load_app(config_path)

    from tn811.db import get_engine, get_session
    from tn811.models import ORMTicket
    from tn811.normalize.tickets import _compute_utility_rollups, _parse_date_layered
    from tn811.portal.detail import parse_detail_html

    # ── Migrate schema: add new columns if the DB predates this feature ──────
    # Always run — migration is idempotent and required even for dry-run reads
    _migrate_add_utility_columns(get_engine())

    base_url = cfg.portal.base_url

    with get_session() as session:
        tickets = (
            session.query(ORMTicket)
            .filter(ORMTicket.source_html_path.isnot(None))
            .all()
        )

    click.echo(
        f"Found {len(tickets)} tickets with saved HTML snapshots "
        f"{'[DRY RUN]' if dry_run else ''}"
    )

    updated = skipped = errors = 0
    now = datetime.now(timezone.utc)

    with get_session() as session:
        for orm_t in session.query(ORMTicket).filter(ORMTicket.source_html_path.isnot(None)).all():
            html_path = Path(orm_t.source_html_path)
            if not html_path.exists():
                skipped += 1
                continue

            try:
                html = html_path.read_text(encoding="utf-8", errors="replace")
                detail = parse_detail_html(
                    html, orm_t.ticket_number, orm_t.county, base_url
                )

                call_date = _parse_date_layered(orm_t.call_date)
                is_cancelled = bool(orm_t.is_cancelled)

                enriched, rollups = _compute_utility_rollups(
                    detail.utility_statuses, call_date, is_cancelled, now
                )

                if dry_run:
                    click.echo(
                        f"  {orm_t.ticket_number}: "
                        f"utils={len(enriched)} ready={rollups['is_ready_to_dig']} "
                        f"late={rollups['late_utility_codes']} "
                        f"blocking={rollups['blocking_utility_codes']} "
                        f"location={detail.fields.get('location_text')} "
                        f"intersection={detail.fields.get('intersection_text')}"
                    )
                    updated += 1
                    continue

                orm_t.utility_statuses = enriched
                orm_t.is_ready_to_dig = rollups["is_ready_to_dig"]
                orm_t.has_late_utility = rollups["has_late_utility"]
                orm_t.late_utility_codes = rollups["late_utility_codes"]
                orm_t.pending_utility_codes = rollups["pending_utility_codes"]
                orm_t.blocking_utility_codes = rollups["blocking_utility_codes"]

                # Backfill location_text and intersection_text if not yet set
                if detail.fields.get("location_text"):
                    orm_t.location_text = detail.fields["location_text"]
                if detail.fields.get("intersection_text"):
                    orm_t.intersection_text = detail.fields["intersection_text"]

                # Backfill legal_start_date if not yet set
                if not orm_t.legal_start_date and detail.fields.get("legal_start_date"):
                    from tn811.pdf.extractor import parse_date as _pd
                    d = _pd(detail.fields["legal_start_date"])
                    if d:
                        orm_t.legal_start_date = d.isoformat()

                updated += 1

            except Exception as exc:
                logger.error(
                    "reparse-details failed for ticket",
                    extra={"ticket": orm_t.ticket_number, "error": str(exc)},
                    exc_info=exc,
                )
                errors += 1

    click.echo(
        f"\nDone: {updated} updated, {skipped} skipped (missing file), {errors} errors"
    )


def _migrate_add_utility_columns(engine) -> None:
    """Add new utility status columns to the tickets table if they don't exist."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing = {col["name"] for col in inspector.get_columns("tickets")}

    new_columns = [
        ("intersection_text", "TEXT"),
        ("utility_statuses", "JSON"),
        ("is_ready_to_dig", "BOOLEAN DEFAULT 0"),
        ("has_late_utility", "BOOLEAN DEFAULT 0"),
        ("late_utility_codes", "JSON"),
        ("pending_utility_codes", "JSON"),
        ("blocking_utility_codes", "JSON"),
    ]

    added = []
    with engine.connect() as conn:
        for col_name, col_type in new_columns:
            if col_name not in existing:
                conn.execute(text(f"ALTER TABLE tickets ADD COLUMN {col_name} {col_type}"))
                added.append(col_name)
        conn.commit()

    if added:
        logger.info("Migration: added columns", extra={"columns": added})
    else:
        logger.debug("Migration: all columns already present")


if __name__ == "__main__":
    main()
