"""
Delivery engine (PRD §28): for one queued event, fan out to every
active, subscribed endpoint and execute the 11-step process the PRD
lists — load event, load endpoint, check active, check circuit, build
payload, sign, send, measure latency, record attempt, classify, mark
outcome.

Ordering (PRD §39-40): when an event carries entity_id, delivery to a
given (tenant, entity_id) is serialized by the caller via a Redis lock
(see worker/main.py) — this module only guarantees single-message
processing, not cross-message ordering, by design.
"""
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eventmesh_common.ssrf import SSRFValidationError, validate_webhook_url  # reused at send-time too
from eventmesh_auth.signature import build_signature_header
from eventmesh_common.circuit_breaker import CircuitBreaker
from eventmesh_common.classify import Outcome, classify_response
from eventmesh_common.enums import CircuitState, DeliveryStatus, EndpointStatus, EventStatus
from eventmesh_common.retry import ExponentialBackoffPolicy
from eventmesh_config.settings import Settings
from eventmesh_database.models import Delivery, Endpoint, EndpointSubscription, Event
from eventmesh_logging.setup import get_logger
from eventmesh_metrics.registry import (
    circuit_breaker_open_total, delivery_failure_total, delivery_latency_seconds,
    delivery_retry_total, delivery_success_total, deliveries_total,
)

logger = get_logger(component="delivery_engine")


@dataclass
class DeliveryOutcomeSummary:
    endpoint_id: uuid.UUID
    outcome: Outcome
    http_status: int | None


class DeliveryEngine:
    def __init__(self, session_factory: async_sessionmaker, settings: Settings, decrypt_secret_fn, redis_client):
        self._session_factory = session_factory
        self._settings = settings
        self._decrypt_secret = decrypt_secret_fn
        self._redis = redis_client
        self._retry_policy = ExponentialBackoffPolicy(
            base_seconds=settings.retry_base_seconds,
            max_delay_seconds=settings.retry_max_delay_seconds,
            max_attempts_=settings.retry_max_attempts,
            jitter_ratio=settings.retry_jitter_ratio,
        )
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=settings.http_connect_timeout_seconds,
                read=settings.http_read_timeout_seconds,
                write=settings.http_write_timeout_seconds,
                pool=settings.http_total_timeout_seconds,
            ),
            follow_redirects=False,  # PRD §62: max_redirects = 0, documented in ADR
        )

    async def aclose(self):
        await self._http_client.aclose()

    async def process_outbox_payload(self, payload: dict) -> list[DeliveryOutcomeSummary]:
        event_id = uuid.UUID(payload["event_id"])
        tenant_id = uuid.UUID(payload["tenant_id"])
        event_type = payload["type"]
        target_endpoint_id = payload.get("target_endpoint_id")

        summaries: list[DeliveryOutcomeSummary] = []
        async with self._session_factory() as session:  # type: AsyncSession
            event = await session.get(Event, event_id)
            if event is None:
                logger.warning("event_missing_for_delivery", event_id=str(event_id))
                return summaries

            endpoints = await self._resolve_endpoints(session, tenant_id, event_type, target_endpoint_id)
            if not endpoints:
                logger.info("no_subscribed_endpoints", event_id=str(event_id), event_type=event_type)
                return summaries

            event.status = EventStatus.PROCESSING.value
            await session.commit()

        for endpoint_id in endpoints:
            outcome = await self._deliver_to_endpoint(event_id, endpoint_id, attempt_number=1)
            summaries.append(outcome)

        return summaries

    async def _resolve_endpoints(self, session: AsyncSession, tenant_id: uuid.UUID, event_type: str, target_endpoint_id: str | None) -> list[uuid.UUID]:
        if target_endpoint_id:
            ep = await session.get(Endpoint, uuid.UUID(target_endpoint_id))
            return [ep.id] if ep and ep.status == EndpointStatus.ACTIVE.value else []

        result = await session.execute(
            select(Endpoint.id)
            .join(EndpointSubscription, EndpointSubscription.endpoint_id == Endpoint.id)
            .where(
                Endpoint.tenant_id == tenant_id,
                Endpoint.status == EndpointStatus.ACTIVE.value,
                EndpointSubscription.event_type == event_type,
            )
        )
        return [row[0] for row in result.all()]

    async def _deliver_to_endpoint(self, event_id: uuid.UUID, endpoint_id: uuid.UUID, attempt_number: int) -> DeliveryOutcomeSummary:
        async with self._session_factory() as session:
            event = await session.get(Event, event_id)
            endpoint = await session.get(Endpoint, endpoint_id)
            if event is None or endpoint is None:
                return DeliveryOutcomeSummary(endpoint_id, Outcome.PERMANENT_FAILURE, None)

            cb = self._load_circuit_breaker(endpoint)
            if not cb.allow_request():
                logger.info("circuit_open_skipping_delivery", endpoint_id=str(endpoint_id))
                # Re-check later via the scheduler rather than dropping the event.
                await self._schedule_retry(session, event_id, endpoint_id, attempt_number, delay_seconds=self._settings.circuit_open_duration_seconds)
                return DeliveryOutcomeSummary(endpoint_id, Outcome.RETRY, None)

            delivery = Delivery(
                event_id=event_id, endpoint_id=endpoint_id, attempt_number=attempt_number,
                status=DeliveryStatus.DELIVERING.value, started_at=datetime.utcnow(),
            )
            session.add(delivery)
            await session.commit()

            deliveries_total.labels(event_type=event.event_type).inc()

            # Re-validate destination at send-time (DNS can change since
            # registration — PRD §61 rebinding concern).
            try:
                validate_webhook_url(endpoint.url, max_length=self._settings.max_endpoint_url_length)
            except SSRFValidationError as e:
                delivery.status = DeliveryStatus.DLQ.value
                delivery.error_message = f"SSRF validation failed at send-time: {e}"
                delivery.completed_at = datetime.utcnow()
                await session.commit()
                return DeliveryOutcomeSummary(endpoint_id, Outcome.PERMANENT_FAILURE, None)

            secret = self._decrypt_secret(endpoint.secret_encrypted)
            body = json.dumps({
                "event_id": str(event_id),
                "delivery_id": str(delivery.id),
                "attempt_number": attempt_number,
                "type": event.event_type,
                "data": event.payload,
            }).encode()
            sig_header = build_signature_header(secret, body)

            status_code = None
            timed_out = False
            connection_error = False
            start = time.monotonic()
            try:
                resp = await self._http_client.post(
                    endpoint.url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-EventMesh-Signature": sig_header,
                        "X-EventMesh-Event-Id": str(event_id),
                        "X-EventMesh-Delivery-Id": str(delivery.id),
                    },
                )
                status_code = resp.status_code
            except httpx.TimeoutException:
                timed_out = True
            except httpx.TransportError:
                connection_error = True
            latency_ms = int((time.monotonic() - start) * 1000)

            delivery_latency_seconds.labels(event_type=event.event_type).observe(latency_ms / 1000)

            result = classify_response(status_code, timed_out=timed_out, connection_error=connection_error)
            delivery.http_status = status_code
            delivery.latency_ms = latency_ms
            delivery.completed_at = datetime.utcnow()

            if result.outcome == Outcome.SUCCESS:
                delivery.status = DeliveryStatus.DELIVERED.value
                cb.record_success()
                delivery_success_total.labels(event_type=event.event_type).inc()
                await self._maybe_complete_event(session, event_id)
            else:
                delivery.error_message = result.reason
                cb.record_failure()
                if cb.state.state == CircuitState.OPEN:
                    circuit_breaker_open_total.inc()
                delivery_failure_total.labels(event_type=event.event_type, outcome=result.outcome.value).inc()

                if result.outcome == Outcome.PERMANENT_FAILURE:
                    delivery.status = DeliveryStatus.DLQ.value
                else:
                    delay = self._retry_policy.next_delay_seconds(attempt_number)
                    if delay is None:
                        delivery.status = DeliveryStatus.DLQ.value
                    else:
                        delivery.status = DeliveryStatus.RETRYING.value
                        delivery.next_retry_at = datetime.utcnow() + timedelta(seconds=delay)
                        delivery_retry_total.labels(event_type=event.event_type).inc()

            self._save_circuit_breaker(endpoint, cb)
            await session.commit()
            return DeliveryOutcomeSummary(endpoint_id, result.outcome, status_code)

    async def _maybe_complete_event(self, session: AsyncSession, event_id: uuid.UUID) -> None:
        result = await session.execute(
            select(Delivery.status).where(Delivery.event_id == event_id)
        )
        statuses = {row[0] for row in result.all()}
        terminal = {DeliveryStatus.DELIVERED.value, DeliveryStatus.DLQ.value}
        if statuses and statuses.issubset(terminal):
            event = await session.get(Event, event_id)
            if event:
                event.status = EventStatus.COMPLETED.value

    async def _schedule_retry(self, session: AsyncSession, event_id: uuid.UUID, endpoint_id: uuid.UUID, attempt_number: int, delay_seconds: float) -> None:
        delivery = Delivery(
            event_id=event_id, endpoint_id=endpoint_id, attempt_number=attempt_number,
            status=DeliveryStatus.RETRYING.value,
            next_retry_at=datetime.utcnow() + timedelta(seconds=delay_seconds),
        )
        session.add(delivery)
        await session.commit()

    def _load_circuit_breaker(self, endpoint: Endpoint) -> CircuitBreaker:
        cb = CircuitBreaker(
            failure_threshold=self._settings.circuit_failure_threshold,
            open_duration_seconds=self._settings.circuit_open_duration_seconds,
            half_open_max_requests=self._settings.circuit_half_open_max_requests,
        )
        cb.state.state = CircuitState(endpoint.circuit_state)
        return cb

    def _save_circuit_breaker(self, endpoint: Endpoint, cb: CircuitBreaker) -> None:
        endpoint.circuit_state = cb.state.state.value
