# ADR-002: Use PostgreSQL as the canonical datastore

**Context**: Need durable, transactional storage for tenants, events,
deliveries, and the transactional outbox. Must support row-level locking
(`SELECT ... FOR UPDATE SKIP LOCKED`) for safe concurrent outbox publishing
and retry scheduling across multiple replicas.

**Decision**: PostgreSQL 16, accessed via SQLAlchemy 2.x async + asyncpg.
SQLite may only be used for the most trivial local experiments and is never
the target of a migration or a load test.

**Alternatives**: MySQL (weaker JSONB support, no native `SKIP LOCKED` until
relatively recent versions with different semantics), MongoDB (would give up
the strict tenant-isolation guarantees a relational schema with foreign keys
and constraints makes easy to enforce and easy to audit).

**Trade-offs**: PostgreSQL requires an operational investment (backups,
connection pooling, vacuum tuning at scale) that a managed NoSQL store might
avoid, but the outbox pattern specifically depends on transactional
guarantees that only a relational/ACID store provides cleanly.

**Consequences**: `events`, `deliveries`, and `outbox_events` all live in the
same database so the outbox insert can commit atomically with the event
insert (see ADR-005).
