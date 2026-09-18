"""
Outbox publisher (PRD §68).

Runs as a background loop inside the worker process (a single extra
asyncio task) rather than a separate deployable — v1 keeps this simple
per PRD §122 ("do not over-engineer"); splitting it into its own service
is listed as a natural extension if publish throughput ever needs to
scale independently of delivery throughput.

Tolerates duplicate publication: if the process crashes after XADD but
before marking a row PUBLISHED, the same row will be republished on the
next poll. This is safe because downstream (the worker's delivery loop)
treats delivery as at-least-once by design (PRD §38) — a duplicate
enqueue just means a duplicate delivery attempt, which consumers are
told to handle idempotently via `event_id`.
"""
import asyncio
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from eventmesh_common.enums import EventStatus, OutboxStatus
from eventmesh_database.models import Event, OutboxEvent
from eventmesh_logging.setup import get_logger
from eventmesh_messaging.broker import MessageBroker

logger = get_logger(component="outbox_publisher")


class OutboxPublisher:
    def __init__(self, session_factory: async_sessionmaker, broker: MessageBroker, stream_name: str, poll_interval_seconds: float = 0.5, batch_size: int = 50):
        self._session_factory = session_factory
        self._broker = broker
        self._stream_name = stream_name
        self._poll_interval = poll_interval_seconds
        self._batch_size = batch_size
        self._running = False

    async def run_forever(self) -> None:
        self._running = True
        await self._broker.ensure_group  # noop check, actual group ensured by worker
        while self._running:
            try:
                published = await self._publish_batch()
                if published == 0:
                    await asyncio.sleep(self._poll_interval)
            except Exception:  # noqa: BLE001
                logger.exception("outbox_publish_batch_failed")
                await asyncio.sleep(self._poll_interval)

    def stop(self) -> None:
        self._running = False

    async def _publish_batch(self) -> int:
        async with self._session_factory() as session:
            result = await session.execute(
                select(OutboxEvent)
                .where(OutboxEvent.status == OutboxStatus.PENDING.value)
                .order_by(OutboxEvent.created_at)
                .limit(self._batch_size)
                .with_for_update(skip_locked=True)  # multiple publisher replicas can run safely
            )
            rows = result.scalars().all()
            if not rows:
                return 0

            for row in rows:
                try:
                    await self._broker.publish(
                        self._stream_name,
                        {"outbox_id": str(row.id), "payload": json.dumps(row.payload)},
                    )
                    row.status = OutboxStatus.PUBLISHED.value
                    row.published_at = __import__("datetime").datetime.utcnow()

                    event = await session.get(Event, row.event_id)
                    if event is not None and event.status == EventStatus.RECEIVED.value:
                        event.status = EventStatus.QUEUED.value
                except Exception:  # noqa: BLE001
                    row.attempts += 1
                    row.status = OutboxStatus.FAILED.value if row.attempts >= 10 else OutboxStatus.PENDING.value
                    logger.exception("outbox_row_publish_failed", outbox_id=str(row.id), attempts=row.attempts)

            await session.commit()
            return len(rows)
