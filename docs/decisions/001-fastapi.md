# ADR-001: Use FastAPI for the API service

**Context**: Need an async-first Python web framework with strong typing,
automatic OpenAPI generation (PRD §93), and good performance under I/O-bound
load (most of the API's work is waiting on Postgres/Redis).

**Decision**: FastAPI + Pydantic v2 + Uvicorn.

**Alternatives**: Flask (sync-first, would need gevent/eventlet to get
comparable async I/O), Django REST Framework (heavier, ORM lock-in conflicts
with our explicit SQLAlchemy 2.x choice), raw Starlette (would mean
hand-rolling request validation and OpenAPI generation).

**Trade-offs**: FastAPI's dependency-injection system is convenient but adds
a layer of indirection that can make request flow harder to trace than
plain function calls; we mitigate this by keeping route handlers thin and
pushing logic into testable, framework-independent modules
(`packages/common`, `delivery_engine.py`, etc.).

**Consequences**: OpenAPI docs are generated for free at `/docs`. Route
handlers must stay async-safe (no blocking calls without `run_in_executor`).
