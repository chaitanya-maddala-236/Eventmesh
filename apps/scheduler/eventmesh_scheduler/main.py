"""
Scheduler process.

Two responsibilities, run as separate asyncio loops in one lightweight
process (PRD §122 — no need for a dedicated framework at this scale):

1. Retry requeue (PRD §33): find `deliveries` rows with status=RETRYING
   and next_retry_at <= now, and push a fresh delivery task back onto
   Redis Streams. This is what lets a worker return to the pool
   immediately after a failed attempt instead of blocking on
   asyncio.sleep() for up to 32 seconds (explicitly forbidden, PRD §33).

2. Retention cleanup (PRD §105-106): periodically delete expired
   events / deliveries / idempotency keys / outbox rows according to
   configured retention windows.
"""
import asyncio
import json
from datetime import datetime, timedelta

import redis.asyncio as redis
from sqlalchemy import delete, select

from eventmesh_common.enums import DeliveryStatus, OutboxStatus
from eventmesh_config.settings import get_settings
from eventmesh_database.models import Delivery, Event, IdempotencyKey, OutboxEvent
from eventmesh_database.session import make_engine, make_session_factory
from eventmesh_logging.setup import configure_logging, get_logger
from eventmesh_messaging.broker import RedisStreamsBroker

settings = get_settings()
configure_logging("scheduler", settings.log_level)
logger = get_logger()


async def retry_requeue_loop(session_factory, broker: RedisStreamsBroker, interval_seconds: float = 1.0):
    while True:
        try:
            async with session_factory() as session:
                now = datetime.utcnow()
                result = await session.execute(
                    select(Delivery)
                    .where(Delivery.status == DeliveryStatus.RETRYING.value, Delivery.next_retry_at <= now)
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
                due = result.scalars().all()
                for delivery in due:
                    event = await session.get(Event, delivery.event_id)
                    if event is None:
                        continue
                    await broker.publish(settings.worker_stream_name, {
                        "outbox_id": f"retry-{delivery.id}",
                        "payload": json.dumps({
                            "event_id": str(event.id),
                            "tenant_id": str(event.tenant_id),
                            "type": event.event_type,
                            "entity_id": event.entity_id,
                            "sequence": event.sequence_number,
                            "data": event.payload,
                            "created_at": event.created_at.isoformat(),
                            "target_endpoint_id": str(delivery.endpoint_id),
                            "_retry_attempt_number": delivery.attempt_number + 1,
                        }),
                    })
                    delivery.status = DeliveryStatus.QUEUED.value
                if due:
                    logger.info("requeued_retries", count=len(due))
                await session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("retry_requeue_loop_error")
        await asyncio.sleep(interval_seconds)


async def cleanup_loop(session_factory, interval_seconds: float = 3600):
    while True:
        try:
            async with session_factory() as session:
                now = datetime.utcnow()
                await session.execute(
                    delete(IdempotencyKey).where(IdempotencyKey.expires_at < now)
                )
                deliveries_cutoff = now - timedelta(days=settings.retention_deliveries_days)
                await session.execute(
                    delete(Delivery).where(
                        Delivery.status != DeliveryStatus.DLQ.value,
                        Delivery.created_at < deliveries_cutoff,
                    )
                )
                dlq_cutoff = now - timedelta(days=settings.retention_dlq_days)
                await session.execute(
                    delete(Delivery).where(
                        Delivery.status == DeliveryStatus.DLQ.value,
                        Delivery.created_at < dlq_cutoff,
                    )
                )
                events_cutoff = now - timedelta(days=settings.retention_events_days)
                await session.execute(
                    delete(OutboxEvent).where(OutboxEvent.status == OutboxStatus.PUBLISHED.value, OutboxEvent.created_at < events_cutoff)
                )
                await session.execute(
                    delete(Event).where(Event.created_at < events_cutoff)
                )
                await session.commit()
                logger.info("retention_cleanup_ran")
        except Exception:  # noqa: BLE001
            logger.exception("cleanup_loop_error")
        await asyncio.sleep(interval_seconds)


async def main():
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    broker = RedisStreamsBroker(redis_client)
    await broker.ensure_group(settings.worker_stream_name, settings.worker_consumer_group)

    logger.info("scheduler_started")
    await asyncio.gather(
        retry_requeue_loop(session_factory, broker),
        cleanup_loop(session_factory),
    )


if __name__ == "__main__":
    asyncio.run(main())
