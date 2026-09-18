# ADR-011 (supplementary): Outbox publisher runs inside the worker process

**Context**: PRD §68 describes "the Outbox Publisher" without mandating it
be a separate deployable.

**Decision**: v1 runs the publisher as one additional `asyncio` task inside
each worker process rather than as its own service/deployment, per PRD §122
("do not over-engineer... just to make the project sound impressive").

**Trade-offs**: Publish throughput scales with worker replica count rather
than independently — acceptable at this project's target scale. If publish
volume ever needs to scale independently of delivery concurrency, splitting
`OutboxPublisher` into `apps/outbox-publisher/` is a small, well-isolated
change (the class has no worker-specific dependencies).

**Consequences**: Killing all worker replicas also stops outbox publishing
(events will queue up as PENDING outbox rows, not lost, until a worker comes
back).
