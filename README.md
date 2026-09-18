# EventMesh

**Reliable, multi-tenant event & webhook delivery platform.**

Publish once. Deliver reliably. Observe everything.

---

## Status: this is a partial, honest build — read this before anything else

This repository implements EventMesh's **core reliability engine** end to
end as real, working code: event ingestion, the transactional outbox,
Redis Streams delivery, HMAC-signed webhooks, exponential-backoff retries,
per-endpoint circuit breakers, dead-letter handling with replay, SSRF
protection, per-tenant rate limiting, and worker crash recovery. **68 unit
tests pass** covering the retry math, circuit breaker state machine, HTTP
response classification, HMAC signing, API key hashing, and SSRF
validation (`make unit-test`).

What it does **not** yet include, so you don't have to find out the hard
way:

- **No live end-to-end run in this environment.** The code was written and
  unit-tested here, but this sandbox has no network access to run
  `docker compose up` against real Postgres/Redis/Grafana containers, so
  the full acceptance test in the original spec (publish → deliver → fail
  → retry → DLQ → replay → recover from a killed worker) has **not been
  executed against a live stack by me**. Run `make up` yourself and it
  should work — the Alembic migration, models, and service code all import
  and unit-test cleanly — but "should work" and "I watched it work" are
  different claims, and only the second one is safe to make without
  overstating what happened here.
- **No React dashboard.** `apps/dashboard/` is an empty scaffold. All 8
  pages from the spec (Overview, Events, Event Detail, Endpoints, Delivery
  Attempts, DLQ, API Keys, System Health) are undesigned and unbuilt.
- **No k6 load tests, no `docs/performance.md`.** Per the PRD's own Rule 1
  ("never fake benchmark numbers"), no throughput/latency claims are made
  anywhere in this repo, because none have been measured yet.
- **No Grafana dashboards provisioned**, no OpenTelemetry tracing wired up.
  Prometheus metric names exist and are exported at `/metrics`; nothing
  visualizes them yet.
- **No integration/contract/failure/load test suites actually implemented**
  — `tests/integration`, `tests/contract`, `tests/failure`, `tests/load`
  exist as empty directories with the unit-test suite as the only tests
  that currently run. See "What's real vs. scaffolded" below.
- No CI security scanning (bandit/pip-audit/Trivy), no cloud deployment
  docs, no production-grade encryption-key management (see
  `docs/security/threat-model.md` §11 for the specific gap).

If you want the rest, the honest next step is: run `make up`, work through
`docs/architecture/overview.md`, and fill in the gaps above — this repo was
deliberately built to make that straightforward (interfaces like
`MessageBroker` and `RetryPolicy` are already pluggable; the schema and
outbox pattern are already in place).

---

## Problem

Naively POSTing to a webhook when something happens fails in predictable
ways: the consumer is down and the event is lost; the consumer processes
the request but the response is lost, so the producer retries and creates
a duplicate; a burst of events overloads the consumer; events arrive out
of order; and when something silently goes wrong there is no way to answer
"why wasn't my webhook delivered?"

EventMesh exists to solve exactly these five failure modes with a durable,
observable delivery pipeline instead of a bare `httpx.post()` call.

## Architecture

```text
Internet -> Nginx -> API (FastAPI) -> PostgreSQL (durable: event + outbox row, one transaction)
                                            |
                                            v  (outbox publisher polls PENDING rows)
                                      Redis Streams
                                            |
                                ------------+------------
                            Worker 1     Worker 2     Worker 3
                                |           |             |
                                ------- Delivery Engine ---
                                            |
                                  External Webhook
                                      |           |
                                    2xx        4xx/5xx/timeout
                                      |           |
                                  DELIVERED    RETRY -> (scheduler requeues) -> DLQ
```

Full lifecycle diagrams (event flow, retry state machine, crash recovery
sequence) live in [`docs/architecture/overview.md`](docs/architecture/overview.md).

## Features (implemented, not aspirational)

- Multi-tenant isolation enforced per-query on `tenant_id`
- API-key auth (hashed at rest, shown once at creation)
- Event ingestion with the transactional outbox pattern — an event is never
  acknowledged to the caller before it's durably committed
- Redis Streams delivery with consumer groups, bounded worker concurrency,
  and crash recovery via `XAUTOCLAIM`
- HMAC-SHA256 webhook signatures over raw body bytes, with a
  timestamp-based replay-protection scheme documented for consumers
- Exponential backoff with jitter (default: 1/2/4/8/16/32s, 6 attempts)
- Per-endpoint circuit breaker (CLOSED/OPEN/HALF_OPEN), state persisted so
  it's shared across worker replicas
- Dead-letter queue with manual replay (preserves original `event_id`)
- Idempotency-Key support for safe request retries by producers
- Per-tenant rate limiting (Redis fixed-window counter)
- SSRF protection on webhook URLs: scheme allowlist, DNS-resolved
  private/metadata IP-range rejection, re-checked at send-time (not just
  registration-time) to catch DNS rebinding, zero redirects followed
- Retention/cleanup jobs for events, deliveries, and idempotency keys

## Delivery semantics — read this before integrating

> **EventMesh provides durable event persistence and at-least-once
> delivery. It does NOT provide exactly-once delivery. Consumers must be
> idempotent, because duplicate delivery remains possible under failure
> conditions** (a worker crashing after your endpoint returns 200 but
> before EventMesh records that fact is the canonical example).

Every webhook payload includes `event_id`, `delivery_id`, and
`attempt_number`. **De-duplicate on `event_id`.** See ADR-004 and ADR-007
in `docs/decisions/` for the full reasoning.

## Failure handling

| Failure | Behavior |
|---|---|
| PostgreSQL unavailable at ingestion | Event ingestion fails the request (no silent fake-success) rather than pretending the event was accepted |
| Redis unavailable at ingestion | Event is still durably persisted (outbox row committed); publishing is delayed until Redis recovers, nothing is lost |
| Worker crashes mid-delivery | Un-acked message stays in the stream's Pending Entries List; reclaimed by another worker via `XAUTOCLAIM` after the idle threshold |
| Webhook times out / connection error | Treated as retryable |
| Webhook returns 5xx | Retryable |
| Webhook returns 429 / 408 / 409 / 425 | Retryable |
| Webhook returns 400 / 401 / 403 / 422 | Permanent failure, goes to DLQ |
| Webhook returns an undeclared 4xx (404 / 410 / ...) | Permanent failure by default (policy-dependent per spec; documented in `classify.py`) |
| Webhook returns a redirect | Not followed (`max_redirects=0`); treated as permanent failure |
| All 6 attempts exhausted | Delivery moves to DLQ, replayable |

Full reasoning for each row: `packages/common/eventmesh_common/classify.py`.

## API

All endpoints are under `/v1`. Full request/response schemas are
auto-generated at `/docs` (Swagger UI) once the API is running.

```text
POST   /v1/events                         create an event (supports Idempotency-Key header)
GET    /v1/events                         list (cursor-paginated)
GET    /v1/events/{id}
POST   /v1/events/{id}/replay

POST   /v1/endpoints                      register a webhook endpoint (returns signing_secret once)
GET    /v1/endpoints
GET    /v1/endpoints/{id}
PATCH  /v1/endpoints/{id}
DELETE /v1/endpoints/{id}
GET    /v1/endpoints/{id}/deliveries

GET    /v1/deliveries/{id}

GET    /v1/dlq
POST   /v1/dlq/{id}/replay

GET    /v1/health                         liveness -- never depends on Postgres/Redis
GET    /v1/ready                          readiness -- checks Postgres + Redis
GET    /metrics                           Prometheus exposition format
```

Errors are always `{"error": {"code", "message", "request_id"}}` — never a
raw stack trace.

## Local setup

```bash
git clone <this-repo>
cd eventmesh
cp .env.example .env
docker compose up --build
```

Then:

| Service | URL |
|---|---|
| API | http://localhost:8000 |
| API docs (Swagger) | http://localhost:8000/docs |
| Nginx (proxied API) | http://localhost:8080 |
| Demo webhook | http://localhost:9000 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3001 (admin/admin) |

There is no public "create your first tenant" endpoint by design (tenant
creation is an administrative action, not a self-serve API in v1) — seed
one directly against Postgres for local testing:

```sql
INSERT INTO tenants (id, name, plan, status) VALUES (gen_random_uuid(), 'Acme', 'free', 'active');
-- then generate an API key with
-- packages/auth/eventmesh_auth/signature.py::generate_api_key
-- and insert the resulting key_hash / key_prefix into api_keys.
```

A small seed script for this would be a good first contribution — not yet
included (see "Known Limitations").

## Environment variables

See [`.env.example`](.env.example) — every configurable value (timeouts,
retry policy, circuit breaker thresholds, rate limits, retention windows,
worker concurrency) is documented there with its default. Nothing is
hard-coded in source.

## Testing

```bash
make unit-test    # 68 tests, no Docker required -- retry math, circuit
                   # breaker transitions, HTTP response classification,
                   # HMAC signing, API key hashing, SSRF validation
make test          # runs unit-test, then reminds you what else needs `make up`
```

What's real vs. scaffolded, precisely:

| Suite | Status |
|---|---|
| `tests/unit` | **68 tests, passing, run in CI** |
| `tests/integration` | directory exists, no tests written yet |
| `tests/contract` | directory exists, no tests written yet |
| `tests/failure` | directory exists; chaos scenarios from the spec are documented as manual steps in `scripts/demo.sh` (item 6), not yet automated |
| `tests/load` | directory exists, no k6 scripts written yet |

## Observability

- Structured JSON logs (`packages/logging`) with automatic redaction of
  API keys, secrets, tokens, and password-shaped fields.
- Prometheus metrics at `/metrics` on the API service, with the exact
  metric names from the spec (`eventmesh_events_ingested_total`,
  `eventmesh_delivery_latency_seconds`, etc. — see
  `packages/metrics/eventmesh_metrics/registry.py`). **Worker and
  scheduler processes do not yet expose their own `/metrics` HTTP
  endpoint** — see `infrastructure/prometheus/prometheus.yml` for the
  specific gap and what closing it requires.
- Example Prometheus alert rules: `infrastructure/prometheus/alerts.yml`
  (thresholds are starting points, not measured SLAs — see the comment at
  the bottom of that file).

## Security

See [`docs/security/threat-model.md`](docs/security/threat-model.md) for
the full, honest breakdown of what's mitigated and what's a known gap
(the encryption-key-management placeholder in particular — item 11 — is
explicitly not production-ready as shipped).

## Deployment

Local: `docker compose up` (above). Production container deployment
documentation is **not yet written**. The codebase is stateless-API /
horizontally-scalable-worker by construction (no in-memory canonical state
anywhere — see `packages/database/eventmesh_database/session.py` and the
absence of any module-level mutable dict in the app code), which is the
prerequisite for writing that doc, but the doc itself (load balancer
config, managed Postgres/Redis sizing, replica counts) doesn't exist yet.

## Architecture decisions

Eleven ADRs in [`docs/decisions/`](docs/decisions/), covering: FastAPI,
PostgreSQL, Redis Streams, at-least-once delivery, the transactional
outbox, retry strategy, idempotency, the ordering model, the circuit
breaker, SSRF protection, and where the outbox publisher lives.

## Known limitations

Restating and consolidating everything flagged above, plus a few more:

- **Not run end-to-end against live infrastructure in this environment.**
  Every module imports cleanly and 68 unit tests pass; the full
  publish -> deliver -> fail -> retry -> DLQ -> replay -> worker-crash-recovery
  acceptance scenario has not been executed by me against real
  Postgres/Redis/HTTP, because this sandbox cannot reach them. Please run
  it yourself and file issues against anything that doesn't work.
- No React dashboard (all 8 pages unbuilt).
- No load testing, no measured performance numbers anywhere in this repo.
- No Grafana dashboards, no distributed tracing.
- No integration/contract/failure/load automated test suites yet.
- Rate limiting uses a fixed window (documented ~2x burst tolerance at
  window edges), not a sliding window or token bucket.
- Tenant isolation is enforced by per-query discipline, not database-level
  Row-Level Security — see threat model §5.
- Webhook-secret encryption-at-rest uses a placeholder key-derivation
  scheme unsuitable for production as-is — see threat model §11.
- No automated CI security scanning (bandit/pip-audit/Trivy) yet.
- No tenant self-service signup / admin UI; tenant and API-key creation
  must be done directly against the database for now.
- No production deployment documentation (cloud provider specifics, load
  balancer config, managed-service sizing).

## Future work (explicitly deferred, not implemented)

Kafka adapter, Kubernetes deployment, multi-region routing, event
filtering/transformation, batch delivery, gRPC ingestion, schema registry,
event versioning, advanced distributed tracing, multi-region disaster
recovery. None of these are needed to validate the core reliability engine,
which is what this pass focused on.

---

## Repository layout

```text
eventmesh/
|-- apps/            api, worker, scheduler, demo-webhook, dashboard (empty scaffold)
|-- packages/        common, config, database, messaging, auth, logging, metrics
|-- tests/unit/      68 passing tests -- the only suite that currently runs
|-- migrations/      Alembic, hand-written initial revision (see its docstring for why)
|-- infrastructure/  nginx, prometheus, grafana provisioning dirs
|-- docs/            architecture/, decisions/ (11 ADRs), security/
|-- scripts/demo.sh  the 7 demo scenarios from the spec, as a runnable script
`-- docker-compose.yml
```

## Portfolio positioning (once measured, not before)

> Built a distributed event delivery platform with FastAPI, Redis Streams,
> PostgreSQL, and asynchronous workers, implementing transactional outbox
> publishing, retry/backoff with jitter, idempotency, per-endpoint circuit
> breaking, dead-letter handling with replay, HMAC webhook signatures with
> SSRF-hardened delivery, and Prometheus observability.

Performance numbers belong here only after `docs/performance.md` exists
and was generated from a real k6 run — not before.
"# Eventmesh" 
