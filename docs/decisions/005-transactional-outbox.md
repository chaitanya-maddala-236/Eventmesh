# ADR-005: Transactional outbox pattern for event publication

**Context**: Writing to two systems (commit an `events` row to PostgreSQL,
then `XADD` to Redis) is not atomic. A crash between the two leaves either
a "phantom" queue message with no matching DB row, or — worse for this
product — a durably-accepted event that silently never gets queued.

**Decision**: Write an `outbox_events` row in the *same transaction* as the
`events` row. A separate publisher loop (running inside the worker process,
see ADR-008 note in docs/architecture) polls `PENDING` outbox rows with
`SELECT ... FOR UPDATE SKIP LOCKED`, publishes to Redis, and marks them
`PUBLISHED`.

**Alternatives**: Postgres `LISTEN/NOTIFY` plus logical replication /
Debezium-style CDC was considered — more "correct" in some ways (lower
publish latency, no polling) but a meaningfully larger operational surface
(a CDC connector, schema-change sensitivity) for a v1 that PRD §122 asks to
keep simple.

**Trade-offs**: Polling introduces a small publish-latency floor (default
0.5s) versus a push-based CDC approach. The publisher may publish the same
row more than once if it crashes between `XADD` and marking the row
`PUBLISHED` — this is why delivery is at-least-once (ADR-004), not a
regression introduced by the outbox specifically.

**Consequences**: Event ingestion latency (`POST /v1/events` response
time) does NOT include the Redis publish — the HTTP response returns as
soon as the DB transaction commits, which is faster and removes Redis
availability from the request's critical path (if Redis is briefly down,
events still get ingested; see docs/architecture "Expected failure
semantics").
