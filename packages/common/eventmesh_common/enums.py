"""
Canonical enums for EventMesh.

Rule (PRD §27): never use arbitrary strings for state throughout the
codebase. Every module that needs a delivery/event/outbox/circuit state
imports from here.
"""
from enum import Enum


class EventStatus(str, Enum):
    RECEIVED = "received"      # persisted, outbox row written, not yet published to the stream
    QUEUED = "queued"          # outbox publisher pushed it onto Redis Streams
    PROCESSING = "processing"  # at least one delivery attempt is in flight
    COMPLETED = "completed"    # all subscribed endpoints reached a terminal state (delivered or DLQ)


class DeliveryStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    RETRYING = "retrying"
    FAILED = "failed"          # permanently failed this attempt cycle, about to become DLQ
    DLQ = "dlq"


class EndpointStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"      # user-disabled
    SUSPENDED = "suspended"    # system-disabled (e.g. repeated abuse / invalid target)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class OutboxStatus(str, Enum):
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"


class TenantPlan(str, Enum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class TenantStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class Role(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


# Response classification (PRD §30) — which HTTP statuses from a webhook
# target are treated as retryable vs permanent. This is intentionally a
# plain dict, not a giant if/elif tree, so it's a single place to audit
# and unit-test.
PERMANENT_4XX = {400, 401, 403, 422}
RETRYABLE_4XX = {408, 409, 425, 429}
# Anything else in 4xx (e.g. 404, 410) is treated as policy-dependent and
# defaults to "permanent" — see classify_response() in delivery/classify.py
# for the documented rationale.
