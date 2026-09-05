PYTHON ?= python3
export PYTHONPATH := src

.PHONY: help install test test-fast demo demo-small fixtures lint dagster db-up db-down clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install the package and its dev extras
	$(PYTHON) -m pip install -e ".[dev,orchestration]"

test: ## Run the full suite (needs Postgres)
	$(PYTHON) -m pytest

test-fast: ## Run only the tests that need no database
	$(PYTHON) -m pytest -m "not db"

demo: ## Build the full synthetic demo and render the pack
	$(PYTHON) scripts/demo.py --scale demo --reset

demo-small: ## The same path at fixture scale, in seconds
	$(PYTHON) scripts/demo.py --scale small --reset

fixtures: ## Regenerate adapter fixtures and golden canonical output
	$(PYTHON) scripts/regen_fixtures.py

dagster: ## Run the Dagster UI against the local control plane
	dagster dev -m fracture.orchestration.definitions

db-up: ## Start local Postgres (docker compose)
	docker compose up -d postgres

db-down:
	docker compose down

clean:
	rm -rf out .pytest_cache **/__pycache__
