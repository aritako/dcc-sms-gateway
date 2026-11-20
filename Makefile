ifneq (,$(wildcard .env))
include .env
export
endif

.PHONY: dev migrate seed test test-all lint typecheck

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

lint: ## Lint the gateway
	cd gateway && uv run ruff check .

typecheck: ## Type-check the gateway
	cd gateway && uv run mypy app
