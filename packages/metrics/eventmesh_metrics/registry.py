"""
Prometheus metrics (PRD §51). Labels are deliberately low-cardinality:
`tenant_plan`, `event_type`, `status`, `http_status_class` are fine;
`event_id` / `request_id` / `user_id` / raw `tenant_id` as labels are
explicitly forbidden by the PRD and are NOT used here.
"""
from prometheus_client import Counter, Gauge, Histogram

events_ingested_total = Counter(
    "eventmesh_events_ingested_total", "Events successfully ingested", ["event_type"]
)
events_failed_total = Counter(
    "eventmesh_events_failed_total", "Events that failed ingestion validation", ["reason"]
)
deliveries_total = Counter(
    "eventmesh_deliveries_total", "Delivery attempts made", ["event_type"]
)
delivery_success_total = Counter(
    "eventmesh_delivery_success_total", "Delivery attempts that succeeded", ["event_type"]
)
delivery_failure_total = Counter(
    "eventmesh_delivery_failure_total", "Delivery attempts that failed (retry or permanent)", ["event_type", "outcome"]
)
delivery_retry_total = Counter(
    "eventmesh_delivery_retry_total", "Delivery attempts that resulted in a scheduled retry", ["event_type"]
)
delivery_latency_seconds = Histogram(
    "eventmesh_delivery_latency_seconds", "Webhook delivery attempt latency", ["event_type"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
queue_depth = Gauge(
    "eventmesh_queue_depth", "Approximate number of pending stream entries"
)
queue_oldest_age_seconds = Gauge(
    "eventmesh_queue_oldest_age_seconds", "Age in seconds of the oldest unprocessed stream entry"
)
active_workers = Gauge(
    "eventmesh_active_workers", "Number of worker processes currently reporting heartbeats"
)
rate_limit_rejections_total = Counter(
    "eventmesh_rate_limit_rejections_total", "Requests rejected due to rate limiting", ["scope"]
)
circuit_breaker_open_total = Counter(
    "eventmesh_circuit_breaker_open_total", "Number of times a circuit breaker transitioned to OPEN"
)
