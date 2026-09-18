# ADR-009: Per-endpoint circuit breaker

**Context**: A single consistently-failing endpoint (wrong URL, decommissioned
service) should not consume a disproportionate share of worker capacity and
outbound-connection slots retrying it, starving healthy endpoints.

**Decision**: Standard three-state breaker (CLOSED / OPEN / HALF_OPEN) per
endpoint, with state persisted on the `endpoints.circuit_state` column so it
survives worker restarts and is visible across all worker replicas.
`failure_threshold=5`, `open_duration=30s`, `half_open_max_requests=1` by
default (PRD §42-43 exact numbers).

**Alternatives**: A purely in-memory (per-worker-process) breaker was
considered and rejected — with N worker replicas, each would independently
need to accumulate 5 failures before opening, meaning a "down" endpoint
could still receive up to `5 * N` requests before any breaker opens.
Persisting state centrally avoids this.

**Trade-offs**: Persisting circuit state in Postgres on every transition
adds a small write per state change (not per request) — acceptable, since
transitions are rare relative to delivery attempts.

**Consequences**: When OPEN, the delivery engine does not drop the event —
it schedules a retry after the cooldown window rather than silently
discarding work (see `delivery_engine.py::_deliver_to_endpoint`).
