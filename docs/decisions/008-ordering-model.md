# ADR-008: Best-effort per-entity ordering, not global ordering

**Context**: Some producers need "event 2 for order #123 must not be
delivered before event 1 for order #123" while not caring at all about
ordering across different orders or different tenants.

**Decision**: Ordering is opt-in via `entity_id` + `sequence_number` on the
event. Delivery to a given `(tenant_id, entity_id)` pair is serialized —
the worker will not start attempt N+1's delivery to any endpoint while
attempt N for the same entity is still in flight or awaiting retry.
EventMesh makes NO ordering guarantee across different `entity_id` values,
and none at all for events that omit `entity_id`.

**Alternatives**: Redis Streams' own partition-by-key routing (multiple
streams, one per hash bucket) was considered for stronger physical
ordering guarantees at higher throughput; deferred to v2 because the v1
per-entity lock approach is simpler to reason about and sufficient at the
throughput this project targets (PRD §122).

**Trade-offs**: A slow or down endpoint for one entity can delay later
events for *that same entity* (by design — that's what ordering means) but
must not block unrelated entities; this isolation boundary is the main
thing to verify under failure testing.

**Consequences**: "Global ordering" must never appear in marketing copy or
docs describing this system (PRD §40 explicit instruction).
