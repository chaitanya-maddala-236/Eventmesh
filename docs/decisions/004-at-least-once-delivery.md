# ADR-004: At-least-once delivery, not exactly-once

**Context**: Any system that can (a) crash between "send request" and
"receive response", or (b) crash after committing a database write but
before acknowledging a queue message, cannot distinguish "the webhook was
never called" from "the webhook was called and we just didn't find out"
without a fully synchronous two-phase-commit across the network boundary —
which is not achievable against an arbitrary third-party HTTP endpoint.

**Decision**: EventMesh guarantees **at-least-once** delivery. It never
claims exactly-once. Consumers are given `event_id` (stable across retries)
specifically so they can de-duplicate on their side.

**Alternatives considered and rejected**: "Best-effort exactly-once" via
response-based de-duplication was considered and rejected — it would give a
false sense of a stronger guarantee than the system can actually uphold
under timeout/partial-failure conditions (PRD §116, explicit requirement).

**Trade-offs**: Consumers must do a small amount of extra work (an
idempotency check keyed on `event_id`) that they would not need with a true
exactly-once system — a burden EventMesh pushes to the edge deliberately,
because pushing it to the edge is the only place it can be correctly
implemented (the consumer knows what "already processed" means for its own
side effects; EventMesh does not).

**Consequences**: This must be stated plainly in the README and in API
documentation, not softened into "we handle this for you."
