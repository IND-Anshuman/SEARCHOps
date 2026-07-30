.PHONY: help install sync lint fmt typecheck test test-unit test-integration test-cov clean dev docs

PYTHON := uv run python
PYTEST  := uv run pytest
RUFF    := uv run ruff
MYPY    := uv run mypy

# ── Default ───────────────────────────────────────────────────────────────────
help: ## Show this help message
	@echo "SEARCHOps — Available make targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Setup ─────────────────────────────────────────────────────────────────────
install: ## Create venv and install all dependencies via uv
	uv venv
	uv sync --all-extras

sync: ## Sync dependencies from lock file
	uv sync --all-extras

hooks: ## Install pre-commit hooks
	uv run pre-commit install
	uv run pre-commit install --hook-type commit-msg

# ── Code Quality ──────────────────────────────────────────────────────────────
lint: ## Run ruff linter
	$(RUFF) check src/ tests/

lint-fix: ## Run ruff linter with auto-fix
	$(RUFF) check --fix src/ tests/

fmt: ## Format code with ruff
	$(RUFF) format src/ tests/

fmt-check: ## Check formatting without changes
	$(RUFF) format --check src/ tests/

typecheck: ## Run mypy type checker
	$(MYPY) src/searchops

check: lint fmt-check typecheck ## Run all checks without modifying files

# ── Testing ───────────────────────────────────────────────────────────────────
test: ## Run all tests
	$(PYTEST)

test-unit: ## Run only unit tests (no infrastructure required)
	$(PYTEST) -m unit --no-cov

test-integration: ## Run integration tests (requires running infrastructure)
	$(PYTEST) -m integration

test-agent: ## Run agent tests
	$(PYTEST) -m agent

test-graph: ## Run LangGraph tests
	$(PYTEST) -m graph

test-cov: ## Run tests with coverage report
	$(PYTEST) --cov=src/searchops --cov-report=html --cov-report=term-missing

test-fast: ## Run tests in parallel (requires pytest-xdist)
	$(PYTEST) -n auto -m unit

# ── Development ───────────────────────────────────────────────────────────────
dev: ## Start the API server in development mode
	$(PYTHON) -m uvicorn searchops.api.main:app --reload --host 0.0.0.0 --port 8000

check-env: ## Validate environment variables
	$(PYTHON) scripts/check_env.py

# ── Database ──────────────────────────────────────────────────────────────────
db-upgrade: ## Apply all Alembic migrations
	$(PYTHON) -m alembic upgrade head

db-downgrade: ## Rollback one Alembic migration
	$(PYTHON) -m alembic downgrade -1

db-revision: ## Create a new Alembic migration (usage: make db-revision MSG="your message")
	$(PYTHON) -m alembic revision --autogenerate -m "$(MSG)"

db-history: ## Show Alembic migration history
	$(PYTHON) -m alembic history

# ── Docker ────────────────────────────────────────────────────────────────────
docker-up: ## Start all infrastructure services
	docker compose up -d

docker-down: ## Stop all infrastructure services
	docker compose down

docker-logs: ## Follow logs from all containers
	docker compose logs -f

docker-ps: ## Show running containers
	docker compose ps

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean: ## Remove build artifacts and caches
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null; true
	find . -type f -name "*.pyc" -delete 2>/dev/null; true
	find . -type f -name "*.pyo" -delete 2>/dev/null; true
	rm -rf .coverage htmlcov .pytest_cache .mypy_cache dist build *.egg-info

# ── Documentation ─────────────────────────────────────────────────────────────
docs: ## Build documentation
	uv run mkdocs build

docs-serve: ## Serve documentation locally
	uv run mkdocs serve
