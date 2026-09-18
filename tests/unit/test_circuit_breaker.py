from eventmesh_common.circuit_breaker import CircuitBreaker
from eventmesh_common.enums import CircuitState


def make_clock():
    """Returns (clock_fn, advance_fn) for deterministic time control."""
    state = {"t": 0.0}

    def clock():
        return state["t"]

    def advance(seconds):
        state["t"] += seconds

    return clock, advance


def test_starts_closed_and_allows_requests():
    cb = CircuitBreaker()
    assert cb.state.state == CircuitState.CLOSED
    assert cb.allow_request() is True


def test_opens_after_failure_threshold():
    cb = CircuitBreaker(failure_threshold=5)
    for _ in range(4):
        cb.record_failure()
        assert cb.state.state == CircuitState.CLOSED
    cb.record_failure()
    assert cb.state.state == CircuitState.OPEN


def test_open_circuit_blocks_requests_until_cooldown():
    clock, advance = make_clock()
    cb = CircuitBreaker(failure_threshold=1, open_duration_seconds=30, _clock=clock)
    cb.record_failure()
    assert cb.state.state == CircuitState.OPEN
    assert cb.allow_request() is False

    advance(29)
    assert cb.allow_request() is False  # still within cooldown

    advance(2)  # total 31s elapsed
    assert cb.allow_request() is True
    assert cb.state.state == CircuitState.HALF_OPEN


def test_half_open_allows_single_probe_only():
    clock, advance = make_clock()
    cb = CircuitBreaker(failure_threshold=1, open_duration_seconds=10, half_open_max_requests=1, _clock=clock)
    cb.record_failure()
    advance(11)
    assert cb.allow_request() is True   # the one probe
    assert cb.allow_request() is False  # second concurrent request blocked


def test_successful_probe_closes_circuit():
    clock, advance = make_clock()
    cb = CircuitBreaker(failure_threshold=1, open_duration_seconds=10, _clock=clock)
    cb.record_failure()
    advance(11)
    assert cb.allow_request() is True
    cb.record_success()
    assert cb.state.state == CircuitState.CLOSED
    assert cb.state.consecutive_failures == 0


def test_failed_probe_reopens_circuit():
    clock, advance = make_clock()
    cb = CircuitBreaker(failure_threshold=1, open_duration_seconds=10, _clock=clock)
    cb.record_failure()
    advance(11)
    assert cb.allow_request() is True
    cb.record_failure()
    assert cb.state.state == CircuitState.OPEN
    assert cb.state.opened_at == clock()  # reopen timer reset


def test_success_resets_consecutive_failures_while_closed():
    cb = CircuitBreaker(failure_threshold=5)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    assert cb.state.consecutive_failures == 0
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.state.state == CircuitState.CLOSED  # only 4 consecutive
