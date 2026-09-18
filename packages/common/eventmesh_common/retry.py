"""
Retry / backoff policies.

PRD §31-33: exponential backoff with jitter, max 6 attempts by default,
computed as data (next_retry_at) rather than long-lived asyncio.sleep()
calls, so a scheduler can requeue work without tying up a worker slot.
"""
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass


class RetryPolicy(ABC):
    @abstractmethod
    def next_delay_seconds(self, attempt_number: int) -> float | None:
        """
        Return the delay (in seconds) before `attempt_number + 1` should
        run, given that `attempt_number` just failed. Return None if no
        further attempts should be made (i.e. attempt_number was the
        last allowed attempt -> caller should move the delivery to DLQ).

        attempt_number is 1-indexed (the first attempt is attempt 1).
        """
        raise NotImplementedError

    @abstractmethod
    def max_attempts(self) -> int:
        raise NotImplementedError


@dataclass
class ExponentialBackoffPolicy(RetryPolicy):
    """
    delay = min(max_delay, base * 2^(attempt_number - 1))
    then delay = delay * uniform(1 - jitter_ratio, 1 + jitter_ratio)

    Defaults reproduce the PRD §31 sequence: 1, 2, 4, 8, 16, 32 seconds
    (before jitter) for 6 total attempts.
    """
    base_seconds: float = 1.0
    max_delay_seconds: float = 32.0
    max_attempts_: int = 6
    jitter_ratio: float = 0.2  # +/- 20%
    _rand: random.Random = None  # injectable for deterministic tests

    def __post_init__(self):
        if self._rand is None:
            self._rand = random.Random()

    def max_attempts(self) -> int:
        return self.max_attempts_

    def next_delay_seconds(self, attempt_number: int) -> float | None:
        if attempt_number >= self.max_attempts_:
            return None
        raw = min(self.max_delay_seconds, self.base_seconds * (2 ** (attempt_number - 1)))
        if self.jitter_ratio <= 0:
            return raw
        low = raw * (1 - self.jitter_ratio)
        high = raw * (1 + self.jitter_ratio)
        return max(0.0, self._rand.uniform(low, high))


@dataclass
class FixedBackoffPolicy(RetryPolicy):
    """Future/optional policy mentioned in PRD §121."""
    delay_seconds: float = 5.0
    max_attempts_: int = 6

    def max_attempts(self) -> int:
        return self.max_attempts_

    def next_delay_seconds(self, attempt_number: int) -> float | None:
        if attempt_number >= self.max_attempts_:
            return None
        return self.delay_seconds


@dataclass
class LinearBackoffPolicy(RetryPolicy):
    """Future/optional policy mentioned in PRD §121."""
    base_seconds: float = 2.0
    max_attempts_: int = 6

    def max_attempts(self) -> int:
        return self.max_attempts_

    def next_delay_seconds(self, attempt_number: int) -> float | None:
        if attempt_number >= self.max_attempts_:
            return None
        return self.base_seconds * attempt_number
