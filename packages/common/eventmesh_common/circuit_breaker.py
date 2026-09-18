"""
Per-endpoint circuit breaker.

This class is pure/in-memory and side-effect free on purpose: the worker
is responsible for loading state from Redis before constructing it and
persisting state after transitioning. Keeping the state machine itself
free of I/O makes it trivially unit-testable (PRD §76).
"""
import time
from dataclasses import dataclass, field

from eventmesh_common.enums import CircuitState


@dataclass
class CircuitBreakerState:
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at: float | None = None
    half_open_probe_in_flight: bool = False


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    open_duration_seconds: float = 30.0
    half_open_max_requests: int = 1
    _clock: callable = field(default=time.monotonic)
    state: CircuitBreakerState = field(default_factory=CircuitBreakerState)

    def allow_request(self) -> bool:
        """Whether a new delivery attempt may be sent right now."""
        if self.state.state == CircuitState.CLOSED:
            return True

        if self.state.state == CircuitState.OPEN:
            if self.state.opened_at is not None and (self._clock() - self.state.opened_at) >= self.open_duration_seconds:
                self._transition_to_half_open()
                return self.allow_request()
            return False

        if self.state.state == CircuitState.HALF_OPEN:
            if self.state.half_open_probe_in_flight:
                return False
            self.state.half_open_probe_in_flight = True
            return True

        return False

    def record_success(self) -> None:
        if self.state.state == CircuitState.HALF_OPEN:
            self._transition_to_closed()
        else:
            self.state.consecutive_failures = 0
            self.state.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        if self.state.state == CircuitState.HALF_OPEN:
            self._transition_to_open()
            return

        self.state.consecutive_failures += 1
        if self.state.consecutive_failures >= self.failure_threshold:
            self._transition_to_open()

    def _transition_to_open(self) -> None:
        self.state.state = CircuitState.OPEN
        self.state.opened_at = self._clock()
        self.state.half_open_probe_in_flight = False

    def _transition_to_half_open(self) -> None:
        self.state.state = CircuitState.HALF_OPEN
        self.state.half_open_probe_in_flight = False

    def _transition_to_closed(self) -> None:
        self.state.state = CircuitState.CLOSED
        self.state.consecutive_failures = 0
        self.state.opened_at = None
        self.state.half_open_probe_in_flight = False
