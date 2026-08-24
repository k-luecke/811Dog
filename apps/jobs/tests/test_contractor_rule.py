"""
Tests for the prime-contractor classification story:

  1. `_worth_detail_fetch` in cli.py — the row-level pre-filter deciding whether
     a search-results row is worth fetching a detail page for. A prime
     contractor named in WorkDoneFor must trigger a full fetch at scrape time,
     so tickets filed by its subcontractors are not silently ingested row-only.

  2. `rescore` CLI command — re-runs the current relevance rules over every
     ticket already in the DB, so a rule change surfaces existing tickets
     without a rescrape.

All contractor and work-type signals come from config; the fixture below stands
in for a deployment's own `relevance` section.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from tn811.cli import _worth_detail_fetch
from tn811.config import ContractorRule, RelevanceConfig

# Stands in for a deployment's configured relevance section.
_RELEVANCE = RelevanceConfig(
    detail_fetch_done_for=["northstar fiber", "meridian cable"],
    detail_fetch_work_types=["fiber optic", "ftth", "fiber instl", "fiber bury"],
    contractor_rule=ContractorRule(
        contractor_keywords=["north ridge", "coastal underground", "lakeside"],
        work_keywords=["fiber", "conduit", "telecom", "boring"],
    ),
)


def _row(
    *,
    work_done_for: str | None = None,
    excavator: str | None = None,
    work_type: str | None = None,
) -> SimpleNamespace:
    """Build a minimal row shim with the fields `_worth_detail_fetch` inspects."""
    return SimpleNamespace(
        work_done_for_raw=work_done_for,
        excavator_name_raw=excavator,
        work_type_raw=work_type,
    )


# ── Pre-filter ────────────────────────────────────────────────────────────────


class TestWorthDetailFetch:
    def test_prime_contractor_in_done_for_triggers_fetch(self):
        assert _worth_detail_fetch(_row(work_done_for="MERIDIAN CABLE CONSTRUCTION"), _RELEVANCE)
        assert _worth_detail_fetch(_row(work_done_for="Meridian Cable/Northstar Fiber"), _RELEVANCE)
        assert _worth_detail_fetch(_row(work_done_for="Meridian Cable"), _RELEVANCE)

    def test_target_in_done_for_still_triggers_fetch(self):
        assert _worth_detail_fetch(_row(work_done_for="NORTHSTAR FIBER"), _RELEVANCE)
        assert _worth_detail_fetch(_row(work_done_for="NORTHSTAR FIBER 6459663"), _RELEVANCE)

    def test_tracked_contractor_excavator_triggers_fetch(self):
        assert _worth_detail_fetch(_row(excavator="NORTH RIDGE CONTRACTORS"), _RELEVANCE)

    def test_fiber_work_type_triggers_fetch(self):
        assert _worth_detail_fetch(_row(work_type="FIBER INSTL"), _RELEVANCE)
        assert _worth_detail_fetch(_row(work_type="FIBER OPTIC INSTALLATION"), _RELEVANCE)

    def test_unrelated_row_does_not_trigger_fetch(self):
        assert not _worth_detail_fetch(
            _row(
                work_done_for="CENTURY COMMUNITIES",
                excavator="RANDOM PLUMBER",
                work_type="WATER SEWER REPAIR",
            ),
            _RELEVANCE,
        )

    def test_namesake_person_does_not_trigger(self):
        """The 'JEFF MERIDIAN' namesake in done_for is not 'Meridian Cable' — no fetch."""
        assert not _worth_detail_fetch(_row(work_done_for="JEFF MERIDIAN"), _RELEVANCE)


# ── rescore ───────────────────────────────────────────────────────────────────


def test_rescore_flips_previously_unflagged_ticket(in_memory_db, base_config):
    """A row already in the DB with is_relevant=0 must become =1 after
    the Meridian Cable rule is applied via `rescore`."""
    from click.testing import CliRunner

    from tn811.cli import main
    from tn811.db import get_session
    from tn811.models import ORMTicket

    now = datetime.now(timezone.utc)
    with get_session() as session:
        # Simulate a row that was stored at scrape time WITHOUT the Meridian rule
        # (score=0.0, flag=False). Exactly matches Summit Underground's real rows.
        session.add(
            ORMTicket(
                ticket_number="MACTEST001",
                county="Davidson County",
                state="TN",
                call_date="2026-04-20",
                excavator_name="SUMMIT UNDERGROUND",
                work_type="CONDUIT INSTL",
                done_for="MERIDIAN CABLE CONSTRUCTION",
                utility_statuses=[],
                utility_codes=[],
                utility_references=[],
                utility_responses=[],
                is_ready_to_dig=False,
                has_late_utility=False,
                late_utility_codes=[],
                pending_utility_codes=[],
                blocking_utility_codes=[],
                status="active",
                is_cancelled=False,
                relevance_score=0.0,
                relevance_reasons=[],
                is_relevant=False,
                created_at=now,
                updated_at=now,
                latest_content_hash="before",
            )
        )

    # Build a config file the CLI can load
    import yaml
    cfg_path = in_memory_db.paths.parsed_dir + "/test_config.yaml"
    with open(cfg_path, "w") as f:
        yaml.safe_dump(
            {
                "db": {"url": in_memory_db.db.url},
                "paths": {
                    k: getattr(in_memory_db.paths, k)
                    for k in (
                        "raw_html_dir", "raw_pdf_dir", "parsed_dir",
                        "exports_dir", "reminder_preview_dir",
                    )
                },
                "counties": [{"name": "Davidson County", "state": "TN",
                              "portal_search_value": "DAVIDSON"}],
                "relevance": {
                    "score_threshold": 0.45,
                    "positive_rules": [
                        {"id": "done_for_prime_contractor", "field": "done_for",
                         "match_type": "contains", "pattern": "Meridian Cable",
                         "weight": 1.0, "case_sensitive": False},
                    ],
                    "negative_rules": [],
                },
            },
            f,
        )

    runner = CliRunner()
    result = runner.invoke(main, ["rescore", "--config", cfg_path])
    assert result.exit_code == 0, result.output
    assert "+    1  newly flagged as relevant" in result.output, result.output

    # Verify the DB row was actually written
    with get_session() as session:
        row = session.query(ORMTicket).filter_by(ticket_number="MACTEST001").one()
        assert row.is_relevant is True
        assert row.relevance_score >= 0.45
        assert any("done_for_prime_contractor" in r for r in row.relevance_reasons)


def test_rescore_dry_run_does_not_write(in_memory_db):
    """--dry-run must report counts but leave the DB untouched."""
    from click.testing import CliRunner

    from tn811.cli import main
    from tn811.db import get_session
    from tn811.models import ORMTicket

    now = datetime.now(timezone.utc)
    with get_session() as session:
        session.add(
            ORMTicket(
                ticket_number="DRYRUN001",
                county="Davidson County",
                state="TN",
                excavator_name="SUMMIT UNDERGROUND",
                work_type="CONDUIT INSTL",
                done_for="MERIDIAN CABLE CONSTRUCTION",
                utility_statuses=[],
                utility_codes=[],
                utility_references=[],
                utility_responses=[],
                is_ready_to_dig=False,
                has_late_utility=False,
                late_utility_codes=[],
                pending_utility_codes=[],
                blocking_utility_codes=[],
                status="active",
                is_cancelled=False,
                relevance_score=0.0,
                is_relevant=False,
                created_at=now,
                updated_at=now,
                latest_content_hash="x",
            )
        )

    import yaml
    cfg_path = in_memory_db.paths.parsed_dir + "/dry_config.yaml"
    with open(cfg_path, "w") as f:
        yaml.safe_dump(
            {
                "db": {"url": in_memory_db.db.url},
                "paths": {
                    k: getattr(in_memory_db.paths, k)
                    for k in (
                        "raw_html_dir", "raw_pdf_dir", "parsed_dir",
                        "exports_dir", "reminder_preview_dir",
                    )
                },
                "counties": [{"name": "Davidson County", "state": "TN",
                              "portal_search_value": "DAVIDSON"}],
                "relevance": {
                    "score_threshold": 0.45,
                    "positive_rules": [
                        {"id": "done_for_prime_contractor", "field": "done_for",
                         "match_type": "contains", "pattern": "Meridian Cable",
                         "weight": 1.0, "case_sensitive": False},
                    ],
                    "negative_rules": [],
                },
            },
            f,
        )

    runner = CliRunner()
    result = runner.invoke(main, ["rescore", "--config", cfg_path, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "[DRY RUN" in result.output

    with get_session() as session:
        row = session.query(ORMTicket).filter_by(ticket_number="DRYRUN001").one()
        assert row.is_relevant is False  # unchanged
        assert float(row.relevance_score) == 0.0
