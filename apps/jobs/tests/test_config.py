"""
tests/test_config.py — Tests for the configuration loader.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from tn811.config import AppConfig, load_config


class TestLoadConfig:
    def test_loads_example_config(self):
        cfg = load_config("config/monitoring.yaml.example")
        assert isinstance(cfg, AppConfig)

    def test_active_counties(self):
        cfg = load_config("config/monitoring.yaml.example")
        counties = cfg.active_counties
        assert len(counties) >= 2
        names = [c.name for c in counties]
        assert "Davidson County" in names
        assert "Rutherford County" in names

    def test_relevance_rules_loaded(self):
        cfg = load_config("config/monitoring.yaml.example")
        assert len(cfg.relevance.positive_rules) > 0
        assert len(cfg.relevance.negative_rules) > 0

    def test_score_threshold(self):
        cfg = load_config("config/monitoring.yaml.example")
        assert 0.0 < cfg.relevance.score_threshold < 1.0

    def test_missing_config_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config(str(tmp_path / "nonexistent.yaml"))

    def test_env_var_interpolation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_SMTP_HOST", "smtp.example.com")
        config_file = tmp_path / "test.yaml"
        config_file.write_text(
            "reminders:\n  smtp:\n    host: '${TEST_SMTP_HOST}'\n    port: 587\n"
        )
        cfg = load_config(str(config_file))
        assert cfg.reminders.smtp.host == "smtp.example.com"

    def test_empty_counties_if_all_disabled(self, tmp_path):
        config_file = tmp_path / "test.yaml"
        config_file.write_text(
            "counties:\n  - name: 'Test County'\n    state: 'TN'\n"
            "    portal_search_value: 'Test'\n    enabled: false\n"
        )
        cfg = load_config(str(config_file))
        assert cfg.active_counties == []

    def test_db_url_default(self, tmp_path):
        config_file = tmp_path / "test.yaml"
        config_file.write_text("{}")
        cfg = load_config(str(config_file))
        assert "sqlite" in cfg.db.url

    def test_reminder_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REMINDER_TO", "a@example.com,b@example.com")
        config_file = tmp_path / "test.yaml"
        config_file.write_text("reminders:\n  recipients:\n    - 'original@example.com'\n")
        cfg = load_config(str(config_file))
        assert "a@example.com" in cfg.reminders.recipients
        assert "b@example.com" in cfg.reminders.recipients
        assert "original@example.com" not in cfg.reminders.recipients
