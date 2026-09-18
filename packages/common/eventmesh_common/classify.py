"""
HTTP response classification for webhook delivery attempts.

PRD §30 — documented, testable rules instead of ad-hoc branching inside
the delivery engine.
"""
from dataclasses import dataclass
from enum import Enum

from eventmesh_common.enums import PERMANENT_4XX, RETRYABLE_4XX


class Outcome(str, Enum):
    SUCCESS = "success"
    RETRY = "retry"
    PERMANENT_FAILURE = "permanent_failure"


@dataclass(frozen=True)
class ClassificationResult:
    outcome: Outcome
    reason: str


def classify_response(status_code: int | None, timed_out: bool = False, connection_error: bool = False) -> ClassificationResult:
    """
    Classify the result of a single delivery attempt.

    - Network-level failures (timeout, connection refused/reset/DNS) are
      always retryable — we cannot distinguish "consumer down" from
      "transient network blip" and the safe default is to retry.
    - 2xx -> success.
    - 3xx is NOT followed (see ADR on redirect policy) and is treated as
      a permanent failure: a webhook target that redirects is
      misconfigured from EventMesh's point of view.
    - 4xx is split into a documented permanent set and a documented
      retryable set (PRD §30). Anything not explicitly listed (404, 406,
      410, 451, etc.) defaults to permanent: these usually mean "this
      resource/route doesn't exist", which more delivery attempts will
      not fix.
    - 5xx -> retry, on the assumption that server errors are often
      transient (deploys, restarts, overload).
    """
    if timed_out:
        return ClassificationResult(Outcome.RETRY, "request timed out")
    if connection_error:
        return ClassificationResult(Outcome.RETRY, "connection error")
    if status_code is None:
        return ClassificationResult(Outcome.RETRY, "no response received")

    if 200 <= status_code < 300:
        return ClassificationResult(Outcome.SUCCESS, f"HTTP {status_code}")

    if 300 <= status_code < 400:
        return ClassificationResult(
            Outcome.PERMANENT_FAILURE, f"HTTP {status_code} redirect not followed (max_redirects=0)"
        )

    if 400 <= status_code < 500:
        if status_code in RETRYABLE_4XX:
            return ClassificationResult(Outcome.RETRY, f"HTTP {status_code} treated as retryable")
        if status_code in PERMANENT_4XX:
            return ClassificationResult(Outcome.PERMANENT_FAILURE, f"HTTP {status_code} treated as permanent")
        # Undeclared 4xx (404, 406, 410, 451, ...) — policy-dependent per
        # PRD §30. Default to permanent; operators can override per
        # endpoint in a future policy table (see docs/decisions ADR-006).
        return ClassificationResult(Outcome.PERMANENT_FAILURE, f"HTTP {status_code} undeclared 4xx, default permanent")

    if 500 <= status_code < 600:
        return ClassificationResult(Outcome.RETRY, f"HTTP {status_code} server error")

    # Anything outside 1xx-5xx shouldn't happen with a compliant HTTP
    # server, but fail safe by retrying rather than silently dropping.
    return ClassificationResult(Outcome.RETRY, f"unexpected status {status_code}")
