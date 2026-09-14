.PHONY: help install dev req req-dev lint format format-check typecheck test test-cov run cli smoke check-providers docker-build docker-run clean

PYTHON ?= python
PORT ?= 8000
PRODUCT ?= coffee machine for the office

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

## --- setup -----------------------------------------------------------------
install: ## Install runtime dependencies (editable, from pyproject.toml)
	$(PYTHON) -m pip install -e .

dev: ## Install runtime + dev dependencies (editable, from pyproject.toml)
	$(PYTHON) -m pip install -e ".[dev]"

req: ## Install pinned runtime dependencies from requirements.txt
	$(PYTHON) -m pip install -r requirements.txt

req-dev: ## Install pinned runtime + dev dependencies from requirements-dev.txt
	$(PYTHON) -m pip install -r requirements-dev.txt

## --- quality gates ---------------------------------------------------------
lint: ## Run ruff lint checks
	$(PYTHON) -m ruff check src tests scripts

format: ## Apply ruff formatting and import sorting
	$(PYTHON) -m ruff check --fix src tests scripts
	$(PYTHON) -m ruff format src tests scripts

format-check: ## Verify formatting without changing files (CI equivalent)
	$(PYTHON) -m ruff format --check src tests scripts

typecheck: ## Run mypy over the src package
	$(PYTHON) -m mypy

test: ## Run the full test suite
	$(PYTHON) -m pytest

test-cov: ## Run tests with coverage
	$(PYTHON) -m pytest --cov=src --cov-report=term-missing

## --- running ---------------------------------------------------------------
run: ## Start the FastAPI application (factory mode)
	$(PYTHON) -m uvicorn src.main:create_app --factory --host 0.0.0.0 --port $(PORT) --reload

cli: ## Run the CLI (PRODUCT="office chair")
	$(PYTHON) -m src.cli --product "$(PRODUCT)"

smoke: ## End-to-end smoke test with mock providers (no API keys required)
	$(PYTHON) scripts/smoke_test.py

check-providers: ## Report provider credential configuration (add NETWORK=1 for live checks)
	$(PYTHON) scripts/check_providers.py $(if $(NETWORK),--network,)

## --- containers ------------------------------------------------------------
docker-build: ## Build the Docker image
	docker build -t ai-procurement-agent:latest .

docker-run: ## Run the Docker image
	docker run --rm -p $(PORT):8000 --env-file .env ai-procurement-agent:latest

## --- housekeeping ----------------------------------------------------------
clean: ## Remove caches and build artifacts
	$(PYTHON) -c "import shutil; [shutil.rmtree(p, ignore_errors=True) for p in ['.pytest_cache','.mypy_cache','.ruff_cache','.coverage','htmlcov','build','dist']]"
	$(PYTHON) -c "import pathlib, shutil; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"
