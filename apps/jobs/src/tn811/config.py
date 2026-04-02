"""
config.py — Central configuration loader for TN811 Monitor.

Loads monitoring.yaml, resolves environment variable substitutions,
and exposes typed config dataclasses.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ── Environment variable interpolation ───────────────────────────────────────

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _interpolate(value: Any) -> Any:
    """Recursively replace ${VAR} tokens with environment variable values."""
    if isinstance(value, str):
        def replacer(m: re.Match[str]) -> str:
            var = m.group(1)
            result = os.environ.get(var, "")
            if not result:
                import warnings
                warnings.warn(f"Config references unset env var: {var}", stacklevel=2)
            return result
        return _ENV_PATTERN.sub(replacer, value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


# ── Typed config dataclasses ─────────────────────────────────────────────────

@dataclass
class DbConfig:
    url: str = "sqlite:///data/tn811.db"


@dataclass
class PortalConfig:
    base_url: str = "https://gcv3.tn811.com"
    search_path: str = "/geocall/portal"
    search_params: str = "directaccess=find"
    date_window_days: int = 30
    headless: bool = True
    timeout_ms: int = 30000
    max_retries: int = 3
    retry_backoff_base_s: float = 2.0

    @property
    def search_url(self) -> str:
        """Full URL for the portal search page."""
        base = self.base_url.rstrip("/") + self.search_path
        if self.search_params:
            return f"{base}?{self.search_params}"
        return base


@dataclass
class CountyConfig:
    name: str
    state: str
    portal_search_value: str
    enabled: bool = True


@dataclass
class PathsConfig:
    raw_html_dir: str = "data/raw/html"
    raw_pdf_dir: str = "data/raw/pdf"
    parsed_dir: str = "data/parsed"
    exports_dir: str = "data/exports"
    reminder_preview_dir: str = "data/parsed/reminder_previews"

    def ensure_dirs(self) -> None:
        """Create all configured directories if they don't exist."""
        for attr in vars(self).values():
            Path(attr).mkdir(parents=True, exist_ok=True)


@dataclass
class PdfConfig:
    primary_parser: str = "pdfplumber"
    fallback_to_pymupdf: bool = True
    ocr_enabled: bool = False
    tesseract_path: str = "/usr/bin/tesseract"


@dataclass
class RelevanceRule:
    id: str
    field: str
    match_type: str  # contains | exact | regex
    pattern: str
    weight: float
    case_sensitive: bool = False


@dataclass
class RelevanceOverrides:
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)


@dataclass
class RelevanceConfig:
    score_threshold: float = 0.45
    positive_rules: list[RelevanceRule] = field(default_factory=list)
    negative_rules: list[RelevanceRule] = field(default_factory=list)
    overrides: RelevanceOverrides = field(default_factory=RelevanceOverrides)


@dataclass
class GroupingConfig:
    cluster_similarity_threshold: float = 0.60
    group_fields: list[str] = field(default_factory=lambda: ["excavator_name", "work_type", "location_text"])


@dataclass
class SmtpConfig:
    host: str = "localhost"
    port: int = 587
    user: str = ""
    password: str = ""
    use_tls: bool = True
    from_address: str = "tn811-monitor@example.com"
    from_name: str = "TN811 Monitor"


@dataclass
class RemindersConfig:
    lead_days: int = 4
    timezone: str = "America/Chicago"
    recipients: list[str] = field(default_factory=list)
    smtp: SmtpConfig = field(default_factory=SmtpConfig)


@dataclass
class DashboardConfig:
    recent_limit: int = 100
    expiring_limit: int = 100
    changes_limit: int = 50
    failures_limit: int = 50
    expiring_window_days: int = 14


@dataclass
class LoggingConfig:
    level: str = "INFO"
    format: str = "json"
    file: str | None = None


@dataclass
class AppConfig:
    db: DbConfig = field(default_factory=DbConfig)
    portal: PortalConfig = field(default_factory=PortalConfig)
    counties: list[CountyConfig] = field(default_factory=list)
    paths: PathsConfig = field(default_factory=PathsConfig)
    pdf: PdfConfig = field(default_factory=PdfConfig)
    relevance: RelevanceConfig = field(default_factory=RelevanceConfig)
    grouping: GroupingConfig = field(default_factory=GroupingConfig)
    reminders: RemindersConfig = field(default_factory=RemindersConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    @property
    def active_counties(self) -> list[CountyConfig]:
        return [c for c in self.counties if c.enabled]


# ── Loader ────────────────────────────────────────────────────────────────────

def load_config(path: str | Path = "config/monitoring.yaml") -> AppConfig:
    """
    Load and validate configuration from a YAML file.

    Environment variables referenced as ${VAR} in the YAML are resolved
    at load time. Missing env vars produce a warning, not an error, so that
    dry-run and test invocations without SMTP credentials still work.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            "Copy config/monitoring.yaml.example to config/monitoring.yaml and edit it."
        )

    with path.open() as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    raw = _interpolate(raw)

    cfg = AppConfig()

    # db
    if db_raw := raw.get("db"):
        cfg.db = DbConfig(**{k: v for k, v in db_raw.items() if k in DbConfig.__dataclass_fields__})

    # portal
    if portal_raw := raw.get("portal"):
        cfg.portal = PortalConfig(**{k: v for k, v in portal_raw.items() if k in PortalConfig.__dataclass_fields__})

    # counties
    if counties_raw := raw.get("counties"):
        cfg.counties = [
            CountyConfig(**{k: v for k, v in c.items() if k in CountyConfig.__dataclass_fields__})
            for c in counties_raw
        ]

    # paths
    if paths_raw := raw.get("paths"):
        cfg.paths = PathsConfig(**{k: v for k, v in paths_raw.items() if k in PathsConfig.__dataclass_fields__})

    # pdf
    if pdf_raw := raw.get("pdf"):
        cfg.pdf = PdfConfig(**{k: v for k, v in pdf_raw.items() if k in PdfConfig.__dataclass_fields__})

    # relevance
    if rel_raw := raw.get("relevance"):
        pos_rules = [RelevanceRule(**r) for r in rel_raw.get("positive_rules", [])]
        neg_rules = [RelevanceRule(**r) for r in rel_raw.get("negative_rules", [])]
        overrides_raw = rel_raw.get("overrides", {})
        overrides = RelevanceOverrides(
            include=overrides_raw.get("include", []),
            exclude=overrides_raw.get("exclude", []),
        )
        cfg.relevance = RelevanceConfig(
            score_threshold=rel_raw.get("score_threshold", 0.45),
            positive_rules=pos_rules,
            negative_rules=neg_rules,
            overrides=overrides,
        )

    # grouping
    if grp_raw := raw.get("grouping"):
        cfg.grouping = GroupingConfig(**{k: v for k, v in grp_raw.items() if k in GroupingConfig.__dataclass_fields__})

    # reminders
    if rem_raw := raw.get("reminders"):
        smtp_raw = rem_raw.get("smtp", {})
        smtp = SmtpConfig(**{k: v for k, v in smtp_raw.items() if k in SmtpConfig.__dataclass_fields__})
        # Allow REMINDER_TO env override for CI
        env_recipients = os.environ.get("REMINDER_TO", "")
        recipients = (
            [r.strip() for r in env_recipients.split(",") if r.strip()]
            if env_recipients
            else rem_raw.get("recipients", [])
        )
        cfg.reminders = RemindersConfig(
            lead_days=rem_raw.get("lead_days", 4),
            timezone=rem_raw.get("timezone", "America/Chicago"),
            recipients=recipients,
            smtp=smtp,
        )

    # dashboard
    if dash_raw := raw.get("dashboard"):
        cfg.dashboard = DashboardConfig(**{k: v for k, v in dash_raw.items() if k in DashboardConfig.__dataclass_fields__})

    # logging
    if log_raw := raw.get("logging"):
        cfg.logging = LoggingConfig(**{k: v for k, v in log_raw.items() if k in LoggingConfig.__dataclass_fields__})

    return cfg
