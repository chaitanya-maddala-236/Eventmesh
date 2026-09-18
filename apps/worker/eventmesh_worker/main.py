"""
Worker process.

Responsibilities (PRD §69-73):
  - Consume from the Redis Streams consumer group with bounded
    concurrency (asyncio.Semaphore — never unbounded, Rule 5).
  - Only ACK a message after the delivery attempt's outcome is durably
    recorded in PostgreSQL. If the process crashes mid-processing, the
    message stays in the Pending Entries List (PEL) and a periodic
    XAUTOCLAIM sweep lets another worker reclaim and reprocess it
    (PRD §71-72) — this is what makes "kill a worker mid-delivery" safe.
  - Handle SIGTERM by stopping new work, draining in-flight tasks within
    a timeout, then exiting (PRD §73) rather than dying immediately.
"""
import asyncio
import json
import signal
import socket
import uuid

import redis.asyncio as redis

from eventmesh_config.settings import get_settings
from eventmesh_database.session import make_engine, make_session_factory
from eventmesh_logging.setup import configure_logging, get_logger
from eventmesh_messaging.broker import RedisStreamsBroker
from eventmesh_worker.delivery_engine import DeliveryEngine
from eventmesh_worker.outbox_publisher import OutboxPublisher

settings = get_settings()
configure_logging("worker", settings.log_level)
logger = get_logger()


def _decrypt_secret(token: str) -> str:
    import base64
    import hashlib

    from cryptography.fernet import Fernet

    key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_encryption_key.encode()).digest())
    return Fernet(key).decrypt(token.encode()).decode()


class Worker:
    def __init__(self):
        self.consumer_name = f"worker-{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self._shutdown = asyncio.Event()
        self._sem = asyncio.Semaphore(settings.worker_concurrency)  # PRD §44/§103: bounded concurrency
        self._inflight: set[asyncio.Task] = set()
        self._delivery_engine_ref: DeliveryEngine | None = None

    async def run(self):
        engine = make_engine(settings.database_url)
        session_factory = make_session_factory(engine)
        redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        broker = RedisStreamsBroker(redis_client)
        delivery_engine = DeliveryEngine(session_factory, settings, _decrypt_secret, redis_client)
        self._delivery_engine_ref = delivery_engine
        publisher = OutboxPublisher(session_factory, broker, settings.worker_stream_name)

        await broker.ensure_group(settings.worker_stream_name, settings.worker_consumer_group)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._shutdown.set)

        publisher_task = asyncio.create_task(publisher.run_forever())
        recovery_task = asyncio.create_task(self._recovery_loop(broker))
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(redis_client))

        logger.info("worker_started", consumer=self.consumer_name)
        try:
            while not self._shutdown.is_set():
                messages = await broker.consume(
                    settings.worker_stream_name, settings.worker_consumer_group, self.consumer_name,
                    count=settings.worker_batch_size, block_ms=settings.worker_block_ms,
                )
                for msg in messages:
                    task = asyncio.create_task(self._handle_message(broker, delivery_engine, msg))
                    self._inflight.add(task)
                    task.add_done_callback(self._inflight.discard)
        finally:
            logger.info("worker_shutting_down", inflight=len(self._inflight))
            publisher.stop()
            if self._inflight:
                await asyncio.wait(self._inflight, timeout=settings.worker_graceful_shutdown_seconds)
            for t in (publisher_task, recovery_task, heartbeat_task):
                t.cancel()
            await delivery_engine.aclose()
            await redis_client.aclose()
            await engine.dispose()
            logger.info("worker_stopped")

    async def _handle_message(self, broker, delivery_engine: DeliveryEngine, msg) -> None:
        async with self._sem:
            try:
                payload = json.loads(msg.fields["payload"])
                await delivery_engine.process_outbox_payload(payload)
            except Exception:  # noqa: BLE001
                logger.exception("message_processing_failed", message_id=msg.message_id)
                return  # do NOT ack — message stays pending and will be reclaimed/retried
            await broker.acknowledge(settings.worker_stream_name, settings.worker_consumer_group, msg.message_id)

    async def _recovery_loop(self, broker) -> None:
        """PRD §71: periodically reclaim messages abandoned by crashed consumers."""
        while True:
            try:
                await asyncio.sleep(15)
                claimed = await broker.claim_stale(
                    settings.worker_stream_name, settings.worker_consumer_group, self.consumer_name,
                    min_idle_ms=settings.worker_stale_claim_idle_ms,
                )
                if claimed:
                    logger.warning("reclaimed_stale_messages", count=len(claimed))
                    for msg in claimed:
                        task = asyncio.create_task(self._handle_message(broker, self._delivery_engine_ref, msg))
                        self._inflight.add(task)
                        task.add_done_callback(self._inflight.discard)
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001
                logger.exception("recovery_loop_error")

    async def _heartbeat_loop(self, redis_client) -> None:
        from eventmesh_metrics.registry import active_workers
        while True:
            try:
                await redis_client.set(f"heartbeat:worker:{self.consumer_name}", "1", ex=30)
                active_workers.set(await redis_client.eval(
                    "return #redis.call('keys', ARGV[1])", 0, "heartbeat:worker:*"
                ) or 0)
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001
                logger.exception("heartbeat_loop_error")
                await asyncio.sleep(10)


async def main():
    worker = Worker()
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
