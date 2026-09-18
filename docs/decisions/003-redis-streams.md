# ADR-003: Use Redis Streams as the message broker

**Context**: Need a durable, ordered-per-partition queue with consumer
groups so multiple worker replicas can share delivery load, and so a
crashed worker's in-flight messages can be reclaimed (PRD §69-72).

**Decision**: Redis Streams with `XREADGROUP` / `XACK` / `XAUTOCLAIM`.

**Alternatives**: Kafka (explicitly excluded from v1 by PRD §122 —
operationally heavier than this project's scale needs; listed as a v2
option behind the `MessageBroker` interface). An in-memory Python queue is
explicitly forbidden (PRD Rule 6) because it does not survive a process
restart.

**Trade-offs**: Redis Streams durability depends on Redis's own persistence
configuration (AOF/RDB) — if Redis loses unflushed data, in-flight
(un-acked and not-yet-consumed) stream entries can be lost. This is why the
*event* itself is never considered safe until it's committed to PostgreSQL
via the outbox (ADR-005); the stream is a delivery mechanism, not the
system of record.

**Consequences**: `packages/messaging` defines a `MessageBroker` ABC so a
`KafkaBroker` could be added later without touching worker/outbox-publisher
call sites.
