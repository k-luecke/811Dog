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
    """TN811 Monitor — internal relevant ticket tracking tool."""
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
    from tn811.grouping.infer import infer_work_groups
    from tn811.models import (
        ORMParseFailure,
        ORMScrapeRun,
        ORMTicket,
        ORMTicketSnapshot,
        ParseFailureReason,
        ScrapeRunStatus,
        normalized_to_orm_dict,
    )
    from tn811.normalize.tickets import normalize_ticket
    from tn811.pdf.downloader import PDFDownloader
    from tn811.pdf.extractor import extract_fields
    from tn811.pdf.fingerprints import pdf_already_parsed
    from tn811.pdf.parser import PDFParser
    from tn811.portal.browser import browser_context
    from tn811.portal.detail import DetailAdapter
    from tn811.portal.search import SearchAdapter
    from tn811.relevance.matcher import RelevanceMatcher

    now = datetime.now(timezone.utc)
    date_to = now.date()
    date_from = date_to - timedelta(days=cfg.portal.date_window_days)

    matcher = RelevanceMatcher(cfg.relevance)
    downloader = PDFDownloader(cfg.portal, cfg.paths.raw_pdf_dir)
    pdf_parser = PDFParser(cfg.pdf)
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

    stats = {"found": 0, "new": 0, "changed": 0, "unchanged": 0, "pdfs": 0, "failures": 0}

    try:
        async with browser_context(cfg.portal) as ctx:
            for county in counties:
                click.echo(f"  County: {county.name}")
                async for row in search_adapter.search_county(ctx, county, date_from, date_to):
                    stats["found"] += 1
                    try:
                        await _process_ticket_row(
                            row, ctx, cfg, detail_adapter, downloader, pdf_parser,
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

        # Run work-group inference on all relevant-related active tickets
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
                    run.pdfs_downloaded = stats["pdfs"]
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


async def _process_ticket_row(
    row, ctx, cfg, detail_adapter, downloader, pdf_parser, matcher, dry_run, stats, now
):
    from tn811.db import get_session
    from tn811.models import (
        ORMTicket, ORMTicketSnapshot, normalized_to_orm_dict,
    )
    from tn811.normalize.tickets import normalize_ticket
    from tn811.pdf.extractor import extract_fields
    from tn811.pdf.fingerprints import pdf_already_parsed

    # Check if we already know this ticket
    existing_hash = None
    existing_pdf_hash = None
    if not dry_run:
        with get_session() as session:
            existing = session.query(ORMTicket).filter_by(
                ticket_number=row.ticket_number
            ).first()
            if existing:
                existing_hash = existing.latest_content_hash
                existing_pdf_hash = existing.source_pdf_sha256

    # Fetch detail page (always, to discover PDF URL and cancellation)
    detail = None
    if row.detail_url:
        try:
            detail = await detail_adapter.fetch(ctx, row.ticket_number, row.county, row.detail_url)
        except Exception as exc:
            logger.warning(
                "Detail page fetch failed",
                extra={"ticket": row.ticket_number, "error": str(exc)},
            )

    # Download PDF if available
    pdf_path = None
    pdf_sha256 = None
    extracted = None

    if detail and detail.pdf_url and not dry_run:
        from pathlib import Path
        dest = Path(cfg.paths.raw_pdf_dir) / f"{row.ticket_number}.pdf"
        if not pdf_already_parsed(dest, existing_pdf_hash):
            try:
                result = await downloader.download(row.ticket_number, detail.pdf_url)
                pdf_path = str(result.path)
                pdf_sha256 = result.sha256
                stats["pdfs"] += 1

                raw = pdf_parser.parse(result.path)
                extracted = extract_fields(raw.full_text, county_hint=row.county)
            except Exception as exc:
                logger.warning(
                    "PDF download/parse failed",
                    extra={"ticket": row.ticket_number, "error": str(exc)},
                )
        else:
            pdf_path = str(dest)
            pdf_sha256 = existing_pdf_hash

    # Normalize
    ticket = normalize_ticket(row, detail, extracted, pdf_path, pdf_sha256, now)

    # Score relevance
    matcher.apply(ticket)

    if dry_run:
        click.echo(
            f"    [DRY] {ticket.ticket_number} | score={ticket.relevance_score:.2f} "
            f"| relevant={ticket.is_relevant} | status={ticket.status.value}"
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
            # New ticket
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
            # Changed ticket
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
        active_relevant = (
            session.query(ORMTicket)
            .filter(
                ORMTicket.is_relevant == True,  # noqa: E712
                ORMTicket.status == TicketStatus.ACTIVE.value,
            )
            .all()
        )
        normalized = [orm_to_normalized(t) for t in active_relevant]
        infer_work_groups(normalized, cfg.grouping)

        # Write back
        for orm_t, norm_t in zip(active_relevant, normalized):
            orm_t.probable_company = norm_t.probable_company
            orm_t.probable_work_group = norm_t.probable_work_group
            orm_t.probable_crew = norm_t.probable_crew


# ── remind ─────────────────────────────────────────────────────────────────────

@main.command()
@click.option("--config", "config_path", default="config/monitoring.yaml", show_default=True)
@click.option("--dry-run", is_flag=True, help="Write preview to disk instead of sending")
def remind(config_path: str, dry_run: bool):
    """Send expiry reminder emails for relevant tickets expiring in lead_days days."""
    cfg = _load_app(config_path)

    from tn811.db import get_session
    from tn811.models import ORMReminderEvent, ORMTicket, TicketStatus, orm_to_normalized
    from tn811.reminders.emailer import ReminderEmailer
    from tn811.reminders.rules import filter_eligible_tickets, get_target_expiration_date

    target = get_target_expiration_date(cfg.reminders)
    click.echo(f"Reminder check for expiration date: {target} {'[DRY RUN]' if dry_run else ''}")

    with get_session() as session:
        # Load all active relevant tickets
        active = (
            session.query(ORMTicket)
            .filter(
                ORMTicket.is_relevant == True,  # noqa: E712
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
    from tn811.pdf.downloader import PDFDownloader
    from tn811.pdf.parser import PDFParser
    from tn811.relevance.matcher import RelevanceMatcher

    matcher = RelevanceMatcher(cfg.relevance)
    downloader = PDFDownloader(cfg.portal, cfg.paths.raw_pdf_dir)
    pdf_parser = PDFParser(cfg.pdf)
    search_adapter = SearchAdapter(cfg.portal, cfg.paths.raw_html_dir)
    detail_adapter = DetailAdapter(cfg.portal, cfg.paths.raw_html_dir)

    total_stats = {"found": 0, "new": 0, "changed": 0, "unchanged": 0, "failures": 0}

    async with browser_context(cfg.portal) as ctx:
        for county in counties:
            for window_start, window_end in windows:
                click.echo(f"  Scraping {county.name}: {window_start} → {window_end}")
                stats = {"found": 0, "new": 0, "changed": 0, "unchanged": 0, "pdfs": 0, "failures": 0}
                async for row in search_adapter.search_county(ctx, county, window_start, window_end):
                    stats["found"] += 1
                    total_stats["found"] += 1
                    try:
                        await _process_ticket_row(
                            row, ctx, cfg, detail_adapter, downloader, pdf_parser,
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
@click.option("--relevant-only", is_flag=True, default=True)
@click.option("--limit", default=50)
def list_tickets(config_path: str, status: str | None, county: str | None, relevant_only: bool, limit: int):
    """List tickets from the database."""
    cfg = _load_app(config_path)

    from tn811.db import get_session
    from tn811.models import ORMTicket

    with get_session() as session:
        q = session.query(ORMTicket)
        if relevant_only:
            q = q.filter(ORMTicket.is_relevant == True)  # noqa: E712
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


if __name__ == "__main__":
    main()
