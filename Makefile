.PHONY: up down logs test unit-test lint fmt migrate build clean

up:
	docker compose up --build

down:
	docker compose down -v

logs:
	docker compose logs -f api worker scheduler

# Runs only the tests that don't require Docker/Postgres/Redis — pure
# unit tests over retry/backoff, circuit breaker, response classification,
# HMAC signing, API key hashing, SSRF validation. This is what CI runs
# on every push; integration/contract/failure/load tests additionally
# require `make up` (or Testcontainers) and are NOT run by `make test`.
unit-test:
	python -m venv .venv 2>/dev/null || true
	. .venv/bin/activate && pip install -q -e packages/common -e packages/config -e packages/database \
		-e packages/messaging -e packages/auth -e packages/logging -e packages/metrics \
		-e apps/api -e apps/worker -e apps/scheduler -r requirements-dev.txt fastapi httpx cryptography redis
	. .venv/bin/activate && python -m pytest tests/unit -v

test: unit-test
	@echo "Integration/contract/failure/load suites require a running stack: 'make up' first, then see tests/integration, tests/contract, tests/failure, tests/load."

lint:
	. .venv/bin/activate && ruff check packages apps tests

fmt:
	. .venv/bin/activate && ruff format packages apps tests

migrate:
	docker compose run --rm migrate

build:
	docker compose build

clean:
	docker compose down -v --remove-orphans
	rm -rf .venv
