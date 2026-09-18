# EventMesh — Architecture Overview

## Components

- **API** (`apps/api`) — FastAPI service. Authenticates requests, validates
  input, persists events + outbox rows transactionally, serves read
  endpoints, exposes `/metrics`.
- **Worker** (`apps/worker`) — consumes `eventmesh:deliveries` via a Redis
  Streams consumer group, runs the delivery engine (HTTP send, sign,
  classify, circuit-break), and also runs the outbox publisher loop
  (see ADR-011).
- **Scheduler** (`apps/scheduler`) — requeues due retries
  (`deliveries.next_retry_at <= now`) and runs retention cleanup jobs.
- **Demo webhook** (`apps/demo-webhook`) — a controllable HTTP receiver
  for demos and failure testing.
- **PostgreSQL** — system of record: tenants, API keys, endpoints, events,
  deliveries, idempotency keys, outbox.
- **Redis** — Streams (queue), rate-limit counters, circuit-breaker read
  path is DB-backed (see ADR-009), worker heartbeats.

## Event lifecycle

```mermaid
sequenceDiagram
    participant P as Producer
    participant A as API
    participant DB as PostgreSQL
    participant R as Redis Streams
    participant W as Worker
    participant H as Webhook

    P->>A: POST /v1/events
    A->>DB: BEGIN; INSERT events; INSERT outbox_events; COMMIT
    A-->>P: 201 {event_id, status: queued}
    Note over A,DB: Response returns as soon as the DB commits.<br/>Redis is NOT on this critical path (ADR-005).

    loop Outbox publisher (inside worker, polls ~0.5s)
        W->>DB: SELECT ... FOR UPDATE SKIP LOCKED (PENDING outbox rows)
        W->>R: XADD eventmesh:deliveries
        W->>DB: UPDATE outbox_events SET status='published'
    end

    loop Worker consumer loop
        W->>R: XREADGROUP
        W->>DB: load event + subscribed endpoints
        W->>H: POST (signed, timestamped)
        H-->>W: 2xx / 4xx / 5xx / timeout
        W->>DB: INSERT deliveries row with outcome
        W->>R: XACK (only after DB commit)
    end
```

## Retry lifecycle

```mermaid
stateDiagram-v2
    [*] --> DELIVERING
    DELIVERING --> DELIVERED: 2xx
    DELIVERING --> RETRYING: 5xx / timeout / retryable 4xx
    DELIVERING --> DLQ: permanent 4xx / redirect
    RETRYING --> DELIVERING: scheduler requeues at next_retry_at
    RETRYING --> DLQ: max attempts exhausted
    DLQ --> DELIVERING: manual replay
    DELIVERED --> [*]
```

## Worker crash recovery

```mermaid
sequenceDiagram
    participant W1 as Worker 1
    participant R as Redis Streams
    participant W2 as Worker 2

    R->>W1: XREADGROUP (message claimed, in Pending Entries List)
    Note over W1: Worker 1 crashes before XACK
    loop Every 15s
        W2->>R: XAUTOCLAIM (min-idle 60s)
    end
    R-->>W2: reclaims message
    W2->>W2: reprocess (idempotent — consumer dedupes on event_id)
    W2->>R: XACK
```

## Known limitations of this document

This overview describes the design as implemented through Phase 8 of the
PRD's phased plan. The React dashboard, full Grafana provisioning, and
load-test-derived performance figures are not yet built — see the top-level
README "Known Limitations" section for the authoritative list of what is
and is not done.
