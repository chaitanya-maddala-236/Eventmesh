# ADR-006: Exponential backoff with jitter, 6 attempts

**Context**: Need a retry policy that recovers from transient failures
without creating a "thundering herd" of synchronized retries against a
recovering webhook endpoint (which can itself cause a second outage).

**Decision**: `delay = min(max_delay, base * 2^(attempt-1))` with the delay
value then perturbed by up to ±20% (`jitter_ratio=0.2`), for up to 6 total
attempts (`1, 2, 4, 8, 16, 32` seconds before jitter, capped at 32s).
Retries are scheduled as `next_retry_at` rows and requeued by the scheduler
(ADR at docs/decisions on the scheduler split) rather than via
`asyncio.sleep()` inside a worker (explicitly forbidden, PRD §33/Rule 5
concern about tying up worker capacity).

**Alternatives**: Fixed and linear backoff are implemented as alternate
`RetryPolicy` classes (`packages/common/eventmesh_common/retry.py`) for
future use but are not the v1 default — exponential-with-jitter is the
industry-standard choice for this exact "many independent clients retrying
against a shared, possibly-degraded target" problem (see e.g. AWS's and
Google's SRE literature on retry storms).

**Trade-offs**: 6 attempts over roughly a minute means a genuinely-down
endpoint accumulates a full DLQ entry relatively quickly; operators who
want longer patience windows before giving up can raise `RETRY_MAX_ATTEMPTS`
and `RETRY_MAX_DELAY_SECONDS`, at the cost of DLQ visibility being delayed.

**Consequences**: Retry math is unit-tested in isolation
(`tests/unit/test_retry.py`) including a property test asserting that two
policies retrying "the same attempt number" do not land on the identical
delay, as a proxy for "this does not produce synchronized retry storms."
