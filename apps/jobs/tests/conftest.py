"""
conftest.py — Shared pytest fixtures for TN811 Monitor tests.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
HTML_DIR = FIXTURES_DIR / "html"
PDF_DIR = FIXTURES_DIR / "pdf"
CSV_DIR = FIXTURES_DIR / "csv"


@pytest.fixture
def search_results_csv() -> str:
    return (CSV_DIR / "search_results_davidson.csv").read_text()


@pytest.fixture
def search_results_html() -> str:
    return (HTML_DIR / "search_results_davidson.html").read_text()


@pytest.fixture
def detail_html_active() -> str:
    return (HTML_DIR / "detail_TN20240601-100001.html").read_text()


@pytest.fixture
def detail_html_cancelled() -> str:
    return (HTML_DIR / "detail_TN20240604-100004.html").read_text()


@pytest.fixture
def detail_html_live_all_clear() -> str:
    """Live portal format — all utilities Closed with clear/located responses."""
    return (HTML_DIR / "detail_live_all_clear.html").read_text()


@pytest.fixture
def detail_html_live_gfi_open() -> str:
    """Live portal format — GFI and U01 still Open (no response)."""
    return (HTML_DIR / "detail_live_gfi_open.html").read_text()


@pytest.fixture
def detail_html_live_mixed() -> str:
    """Live portal format — delayed GFI, in-conflict ATT, Open MWS."""
    return (HTML_DIR / "detail_live_mixed.html").read_text()


@pytest.fixture
def sample_ticket_text() -> str:
    """Simulate raw text from a TN811 ticket PDF."""
    return """
TN811 TICKET INFORMATION

Ticket Number: TN20240601-100001
County: Davidson
Call Date: 06/01/2024
Legal Start Date: 06/03/2024
Expiration Date: 06/16/2024

Excavator: Acme Fiber LLC
Caller Name: John Smith
Phone: 615-555-0100

Work Type: Telecom - Underground Fiber Installation

Location: 123 Main St, Nashville, TN 37201
Installing underground fiber optic cable along Main St

Remarks: Google Fiber drop bury installation. Boring for fiber conduit.
FTTH residential buildout. Fiber optic installation project.

Utilities Notified:
- Nashville Electric Service
- Metro Water Services
- AT&T
- Comcast
"""


@pytest.fixture
def sample_non_fiber_text() -> str:
    """Simulate raw text from a non-fiber ticket PDF."""
    return """
TN811 TICKET INFORMATION

Ticket Number: TN20240601-200001
County: Davidson
Call Date: 06/01/2024
Expiration Date: 06/16/2024

Excavator: Metro Plumbing Inc
Work Type: Water/Sewer
Location: 789 Elm St, Nashville, TN
Remarks: Water main replacement. No telecom work.
"""


@pytest.fixture
def in_memory_db(tmp_path):
    """Create an in-memory SQLite database for testing."""
    from tn811.config import AppConfig, DbConfig, PathsConfig
    from tn811.db import init_db

    cfg = AppConfig()
    cfg.db = DbConfig(url=f"sqlite:///{tmp_path}/test.db")
    cfg.paths = PathsConfig(
        raw_html_dir=str(tmp_path / "html"),
        raw_pdf_dir=str(tmp_path / "pdf"),
        parsed_dir=str(tmp_path / "parsed"),
        exports_dir=str(tmp_path / "exports"),
        reminder_preview_dir=str(tmp_path / "reminders"),
    )
    cfg.paths.ensure_dirs()
    init_db(cfg)
    return cfg


@pytest.fixture
def base_config():
    """Return a minimal AppConfig suitable for unit tests."""
    from tn811.config import (
        AppConfig, CountyConfig, DbConfig, RelevanceConfig,
        RelevanceRule, RelevanceOverrides, GroupingConfig,
        RemindersConfig, PathsConfig,
    )
    cfg = AppConfig()
    cfg.db = DbConfig(url="sqlite:///:memory:")
    cfg.counties = [
        CountyConfig(name="Davidson County", state="TN", portal_search_value="Davidson"),
        CountyConfig(name="Rutherford County", state="TN", portal_search_value="Rutherford"),
    ]
    cfg.relevance = RelevanceConfig(
        score_threshold=0.45,
        positive_rules=[
            RelevanceRule(id="google_fiber", field="any", match_type="contains",
                          pattern="google fiber", weight=1.0, case_sensitive=False),
            RelevanceRule(id="gfiber", field="any", match_type="contains",
                          pattern="gfiber", weight=1.0, case_sensitive=False),
            RelevanceRule(id="fiber_install", field="any", match_type="regex",
                          pattern=r"fiber\s+install", weight=0.75, case_sensitive=False),
            RelevanceRule(id="ftth", field="any", match_type="contains",
                          pattern="ftth", weight=0.85, case_sensitive=False),
            RelevanceRule(id="drop_bury", field="any", match_type="contains",
                          pattern="drop bury", weight=0.65, case_sensitive=False),
            RelevanceRule(id="fiber_generic", field="any", match_type="contains",
                          pattern="fiber", weight=0.30, case_sensitive=False),
        ],
        negative_rules=[
            RelevanceRule(id="water_only", field="work_type", match_type="regex",
                          pattern=r"^water/sewer$", weight=-0.50, case_sensitive=False),
        ],
        overrides=RelevanceOverrides(include=[], exclude=[]),
    )
    cfg.grouping = GroupingConfig(cluster_similarity_threshold=0.60)
    return cfg


@pytest.fixture
def sample_normalized_ticket():
    """Return a sample NormalizedTicket for testing."""
    from tn811.models import NormalizedTicket
    return NormalizedTicket(
        ticket_number="TN20240601-100001",
        county="Davidson County",
        call_date=date(2024, 6, 1),
        legal_start_date=date(2024, 6, 3),
        expiration_date=date(2024, 6, 16),
        excavator_name="Acme Fiber LLC",
        caller_name="John Smith",
        work_type="Telecom - Underground Fiber Installation",
        location_text="123 Main St, Nashville, TN 37201",
        remarks="Google Fiber drop bury installation. FTTH residential buildout.",
        is_gfiber_related=False,  # will be set by matcher
        relevance_score=0.0,
    )
