.PHONY: help install lint format test test-unit test-integration validate deploy-foundation ingest clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install project in development mode
	pip install -e ".[dev,notebooks]"
	pre-commit install

lint: ## Run linting checks
	ruff check src/ tests/
	black --check src/ tests/

format: ## Auto-format code
	ruff check --fix src/ tests/
	black src/ tests/

test: test-unit ## Run all offline tests

test-unit: ## Run unit tests only
	pytest -m unit tests/

test-integration: ## Run integration tests (requires Databricks)
	pytest -m integration tests/

validate: ## Validate configuration files
	python -c "from fuelsignal.config import load_project_config; load_project_config()"
	python -c "from fuelsignal.config import load_sources_config; load_sources_config()"

deploy-foundation: ## Create Databricks catalog, schemas, and Delta tables
	python scripts/deploy_databricks_foundation.py

ingest: ## Download official sources and populate existing Bronze/Silver tables
	python scripts/run_ingestion_pipeline.py

clean: ## Remove build artifacts
	rm -rf build/ dist/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
