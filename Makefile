ifneq (,$(wildcard .env))
include .env
export
endif

.PHONY: dev migrate seed test test-all lint typecheck load load-test run-1k smoke

COUNT ?= 1000
LOAD_COUNT ?= 10000
API_PORT ?= 8000
BASE_URL ?= http://localhost:$(API_PORT)

dev: ## Start Postgres and Redis in the background
	docker compose up -d postgres redis

migrate: ## Apply database migrations (needs .env)
	cd gateway && uv run alembic upgrade head

seed: ## Insert baseline data (idempotent)
	cd gateway && uv run python -m app.seed

test: ## Run the gateway test suite (needs Postgres and Redis from `make dev`)
	cd gateway && uv run pytest

test-all: ## Run each service's tests in its own venv
	cd gateway && uv run pytest
	cd carrier && uv run pytest

lint: ## Lint the gateway and the load script
	cd gateway && uv run ruff check . ../scripts/load_test.py

typecheck: ## Type-check the gateway and the load script
	cd gateway && uv run mypy app ../scripts/load_test.py

load: ## Enqueue COUNT messages through the API (default 1000)
	uv run --project gateway python scripts/send.py $(COUNT)

load-test: ## Run the load proof (LOAD_COUNT=50 for a smoke run)
	uv run --project gateway python scripts/load_test.py $(LOAD_COUNT)

run-1k: ## Recorded COUNT-message dispatch run, with a PASS/FAIL verdict
	uv run --project gateway python scripts/run_1k.py $(COUNT)

smoke: ## Send one message and poll it to a terminal state (needs a running stack)
	BASE_URL=$(BASE_URL) uv run --project gateway python scripts/smoke.py
