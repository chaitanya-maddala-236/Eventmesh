import random

from eventmesh_common.retry import ExponentialBackoffPolicy, FixedBackoffPolicy, LinearBackoffPolicy


def test_exponential_backoff_sequence_no_jitter():
    policy = ExponentialBackoffPolicy(base_seconds=1, max_delay_seconds=32, max_attempts_=6, jitter_ratio=0)
    delays = [policy.next_delay_seconds(a) for a in range(1, 6)]
    assert delays == [1, 2, 4, 8, 16]


def test_exponential_backoff_caps_at_max_delay():
    policy = ExponentialBackoffPolicy(base_seconds=1, max_delay_seconds=10, max_attempts_=10, jitter_ratio=0)
    assert policy.next_delay_seconds(6) == 10  # would be 32 uncapped
    assert policy.next_delay_seconds(9) == 10


def test_exponential_backoff_returns_none_after_max_attempts():
    policy = ExponentialBackoffPolicy(max_attempts_=6, jitter_ratio=0)
    assert policy.next_delay_seconds(6) is None
    assert policy.next_delay_seconds(7) is None


def test_jitter_stays_within_bounds_and_varies():
    rng = random.Random(42)
    policy = ExponentialBackoffPolicy(base_seconds=1, max_delay_seconds=32, jitter_ratio=0.2, _rand=rng)
    samples = [policy.next_delay_seconds(3) for _ in range(200)]  # raw=4
    assert all(3.2 <= s <= 4.8 for s in samples)
    assert len(set(samples)) > 50  # actually jittering, not constant


def test_jitter_never_negative_even_with_large_ratio():
    rng = random.Random(1)
    policy = ExponentialBackoffPolicy(base_seconds=1, jitter_ratio=1.5, _rand=rng)
    samples = [policy.next_delay_seconds(1) for _ in range(200)]
    assert all(s >= 0 for s in samples)


def test_no_synchronized_retry_storm_property():
    # Two "different events" retrying at the same attempt number should
    # not all land on the exact same next_retry_at instant.
    rng_a = random.Random(1)
    rng_b = random.Random(2)
    policy_a = ExponentialBackoffPolicy(jitter_ratio=0.2, _rand=rng_a)
    policy_b = ExponentialBackoffPolicy(jitter_ratio=0.2, _rand=rng_b)
    assert policy_a.next_delay_seconds(3) != policy_b.next_delay_seconds(3)


def test_fixed_backoff_policy():
    policy = FixedBackoffPolicy(delay_seconds=5, max_attempts_=3)
    assert policy.next_delay_seconds(1) == 5
    assert policy.next_delay_seconds(2) == 5
    assert policy.next_delay_seconds(3) is None


def test_linear_backoff_policy():
    policy = LinearBackoffPolicy(base_seconds=2, max_attempts_=4)
    assert [policy.next_delay_seconds(a) for a in range(1, 4)] == [2, 4, 6]
