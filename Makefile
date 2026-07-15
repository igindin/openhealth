# OpenHealth — one-command developer setup.
# A newcomer runs `make setup` once, then `make check` before shipping.

.DEFAULT_GOAL := help
PY ?= python3

help: ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

onboard: ## Friendly guided setup for non-developers (same as: bash setup.sh).
	bash setup.sh

setup: ## Install OpenHealth (editable) + init a local workspace. Run this first.
	$(PY) -m pip install -e .
	$(PY) -m openhealth init || true
	@command -v pre-commit >/dev/null 2>&1 && pre-commit install || echo "(pre-commit not installed; optional)"
	@echo "Setup done. Try: make check"

test: ## Run the test suite.
	$(PY) -m pytest -q

lint: ## Best-effort lint (ruff if present) + byte-compile check.
	@command -v ruff >/dev/null 2>&1 && ruff check openhealth || echo "(ruff not installed; skipping)"
	$(PY) -m compileall -q openhealth

check: lint test ## Lint + test. Run before you ship.

modules: ## List available health domain modules.
	$(PY) -m openhealth modules

dashboard: ## Rebuild dashboard data from the local workspace + launch the dashboard (macOS).
	-$(PY) ui/web/build_dashboard_data.py --db data/index/openhealth.sqlite3 --out ui/web/data.local.json
	bash ui/web/OpenHealth.command

app: ## Build the macOS desktop app (ui/web/dist/OpenHealth.app).
	$(PY) ui/web/make_macos_app.py

app-install: ## Build OpenHealth.app + install into ~/Applications.
	$(PY) ui/web/make_macos_app.py --install

.PHONY: help onboard setup test lint check modules dashboard app app-install
