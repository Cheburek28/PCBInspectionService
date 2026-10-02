# One entry point for humans, agents and CI. `make check` = everything CI checks.
.DEFAULT_GOAL := help
UV ?= uv
RUN := $(UV) run
SRC := src tests migrations scripts

.PHONY: help install fmt lint typecheck layers test-unit test-int test cov openapi openapi-check \
        migration migrate check up down logs smoke docker-build bench clean

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies and git hooks
	$(UV) sync
	$(RUN) pre-commit install

fmt: ## Format code and apply safe lint fixes
	$(RUN) ruff format $(SRC)
	$(RUN) ruff check --fix $(SRC)

lint: ## Lint and check formatting
	$(RUN) ruff check $(SRC)
	$(RUN) ruff format --check $(SRC)

typecheck: ## mypy --strict
	$(RUN) mypy

layers: ## Architecture contracts (import-linter)
	$(RUN) lint-imports

test-unit: ## Fast tests, no Docker
	$(RUN) pytest tests/unit

test-int: ## Integration tests (Docker: Postgres, Redis via testcontainers)
	$(RUN) pytest tests/integration

test: ## All tests except e2e
	$(RUN) pytest tests/unit tests/integration

cov: ## All tests with coverage gate
	$(RUN) pytest tests/unit tests/integration --cov --cov-report=term --cov-report=xml --cov-fail-under=90

openapi: ## Regenerate openapi.json (the public contract)
	$(RUN) pcbis openapi --out openapi.json

openapi-check: ## Fail if openapi.json is out of date
	$(RUN) pytest -q tests/unit/test_cli.py::test_committed_openapi_is_up_to_date

migration: ## New Alembic revision: make migration m="add column x"
	@test -n "$(m)" || (echo 'usage: make migration m="message"' && exit 1)
	$(RUN) alembic revision --autogenerate -m "$(m)"

migrate: ## Apply migrations to PCBIS_DATABASE_URL
	$(RUN) pcbis migrate

check: lint typecheck layers cov ## Everything CI runs (except docker/e2e)

up: ## Start the dev stack (API on :8000)
	docker compose up -d --build --wait

down: ## Stop the dev stack
	docker compose down

logs: ## Follow dev stack logs
	docker compose logs -f api worker

smoke: ## End-to-end check against a running stack (make up first)
	$(RUN) python scripts/smoke.py --url $${PCBIS_URL:-http://localhost:8000}

docker-build: ## Build the image
	docker build -f docker/Dockerfile -t pcbis:dev .

bench: ## Run the engine on local photos: make bench REF=ref.jpg PHOTOS="a.jpg b.jpg"
	$(RUN) pcbis bench $(REF) $(PHOTOS)

clean: ## Remove caches
	rm -rf .mypy_cache .ruff_cache .pytest_cache .hypothesis .coverage coverage.xml htmlcov
