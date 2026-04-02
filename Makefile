.PHONY: help scrape scrape-dry remind remind-dry build-json export-dashboard backfill dev-dashboard build-dashboard test lint install install-dev

PYTHON := python
JOBS_DIR := apps/jobs
DASHBOARD_DIR := apps/dashboard
CONFIG := config/monitoring.yaml

help:
	@echo "TN811 Monitor — available targets"
	@echo ""
	@echo "  scrape              Full scrape run (last 30 days)"
	@echo "  scrape-dry          Dry run (no writes)"
	@echo "  backfill            Backfill 90 days (chained 30-day windows)"
	@echo "  remind              Send reminder emails"
	@echo "  remind-dry          Preview reminders to disk"
	@echo "  build-json          Rebuild dashboard JSON exports"
	@echo "  export-dashboard    Alias for build-json"
	@echo "  dev-dashboard       Vite dev server"
	@echo "  build-dashboard     Production dashboard build"
	@echo "  test                Run all Python tests"
	@echo "  lint                Ruff + mypy"
	@echo "  install             Install Python deps (prod)"
	@echo "  install-dev         Install Python deps (dev)"
	@echo ""

# ── Python jobs ─────────────────────────────────────────────────────────────

install:
	pip install -e "$(JOBS_DIR)"

install-dev:
	pip install -e "$(JOBS_DIR)[dev]"
	playwright install chromium

scrape:
	$(PYTHON) -m tn811.cli scrape --config $(CONFIG)

scrape-dry:
	$(PYTHON) -m tn811.cli scrape --config $(CONFIG) --dry-run

# Backfill: chained 30-day windows for DAYS total days of history
# Usage: make backfill DAYS=90
DAYS ?= 90
backfill:
	$(PYTHON) -m tn811.cli backfill --config $(CONFIG) --days $(DAYS)

remind:
	$(PYTHON) -m tn811.cli remind --config $(CONFIG)

remind-dry:
	$(PYTHON) -m tn811.cli remind --config $(CONFIG) --dry-run

build-json:
	$(PYTHON) -m tn811.cli build-json --config $(CONFIG)

export-dashboard:
	$(PYTHON) -m tn811.cli export-dashboard --config $(CONFIG)

test:
	cd $(JOBS_DIR) && pytest tests/ -v --tb=short

lint:
	cd $(JOBS_DIR) && ruff check src/ tests/ && mypy src/

# ── Dashboard ────────────────────────────────────────────────────────────────

dev-dashboard:
	cd $(DASHBOARD_DIR) && npm run dev

build-dashboard:
	cd $(DASHBOARD_DIR) && npm run build

# ── Full pipeline ────────────────────────────────────────────────────────────

pipeline: scrape build-json
	@echo "Pipeline complete."

# ── Data management ──────────────────────────────────────────────────────────

clean-exports:
	rm -f data/exports/*.json
	@echo "Exports cleared."

init-db:
	$(PYTHON) -m tn811.cli init-db --config $(CONFIG)

# ── Dev helpers ───────────────────────────────────────────────────────────────

show-config:
	$(PYTHON) -m tn811.cli show-config --config $(CONFIG)

list-tickets:
	$(PYTHON) -m tn811.cli list-tickets --config $(CONFIG) --status active

reset-reminders:
	$(PYTHON) -m tn811.cli reset-reminders --config $(CONFIG) --confirm
