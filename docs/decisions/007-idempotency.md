# ADR-007: Idempotency at both the ingestion and delivery layers

**Context**: Two different duplication risks exist: (1) a producer's HTTP
client retries `POST /v1/events` after a network timeout, potentially
creating two logical events for one real-world occurrence; (2) EventMesh's
own at-least-once delivery (ADR-004) can call a consumer's webhook more
than once for the same event.

**Decision**: (1) is solved with a client-supplied `Idempotency-Key` header,
looked up in a `(tenant_id, key)`-unique `idempotency_keys` table before
creating a new event — a repeat request within the TTL window returns the
original `event_id`. (2) is solved by giving the consumer a stable
`event_id` (and a distinct, always-incrementing `delivery_id`/
`attempt_number`) in every webhook payload, with documentation telling
integrators to de-duplicate on `event_id`.

**Alternatives**: Hashing the request body as an implicit idempotency key
was considered and rejected — two genuinely different events can
legitimately have identical payloads (e.g. two `$0` test transactions), so
an implicit content hash would incorrectly collapse them.

**Trade-offs**: Requires the producer to opt in by sending the header;
EventMesh cannot protect against duplicate submission for a producer that
doesn't send one. This is documented, not silently "solved" only in some
cases.
