"""
Message broker abstraction.

PRD §121 asks for an interface that could later grow a KafkaBroker
implementation without rewriting workers. RedisStreamsBroker is the only
implementation shipped in v1 (PRD §13 — Redis Streams is mandatory, an
in-memory queue is explicitly forbidden as the production queue, Rule 6).
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis


@dataclass
class StreamMessage:
    message_id: str      # broker-assigned id (e.g. Redis Streams "1700000000000-0")
    fields: dict[str, Any]


class MessageBroker(ABC):
    @abstractmethod
    async def publish(self, stream: str, fields: dict[str, Any]) -> str:
        ...

    @abstractmethod
    async def consume(self, stream: str, group: str, consumer: str, count: int, block_ms: int) -> list[StreamMessage]:
        ...

    @abstractmethod
    async def acknowledge(self, stream: str, group: str, message_id: str) -> None:
        ...

    @abstractmethod
    async def claim_stale(self, stream: str, group: str, consumer: str, min_idle_ms: int, count: int) -> list[StreamMessage]:
        """Reclaim messages that another consumer picked up but never acked (crash recovery, PRD §71-72)."""
        ...

    @abstractmethod
    async def ensure_group(self, stream: str, group: str) -> None:
        ...


class RedisStreamsBroker(MessageBroker):
    def __init__(self, redis_client: "redis.Redis"):
        self._redis = redis_client

    async def ensure_group(self, stream: str, group: str) -> None:
        try:
            await self._redis.xgroup_create(name=stream, groupname=group, id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise  # group already exists -> fine, anything else -> real error

    async def publish(self, stream: str, fields: dict[str, Any]) -> str:
        # Redis Streams fields must be str/bytes; caller is responsible
        # for serializing nested structures (e.g. JSON-encoding payload)
        # before calling publish().
        return await self._redis.xadd(stream, fields)

    async def consume(self, stream: str, group: str, consumer: str, count: int = 10, block_ms: int = 5000) -> list[StreamMessage]:
        resp = await self._redis.xreadgroup(
            groupname=group, consumername=consumer,
            streams={stream: ">"}, count=count, block=block_ms,
        )
        return self._parse_stream_response(resp)

    async def acknowledge(self, stream: str, group: str, message_id: str) -> None:
        await self._redis.xack(stream, group, message_id)

    async def claim_stale(self, stream: str, group: str, consumer: str, min_idle_ms: int = 60_000, count: int = 50) -> list[StreamMessage]:
        # XAUTOCLAIM (Redis >= 6.2) both scans pending-entries and
        # reassigns ownership atomically, which is what PRD §71 asks for
        # ("pending message recovery" without a separate scan step).
        result = await self._redis.xautoclaim(
            name=stream, groupname=group, consumername=consumer,
            min_idle_time=min_idle_ms, start_id="0-0", count=count,
        )
        # xautoclaim returns (next_cursor, claimed_messages, deleted_ids)
        _next_cursor, claimed, _deleted = result
        return [
            StreamMessage(message_id=mid, fields=fields)
            for mid, fields in claimed
        ]

    @staticmethod
    def _parse_stream_response(resp) -> list[StreamMessage]:
        messages: list[StreamMessage] = []
        for _stream_name, entries in resp or []:
            for message_id, fields in entries:
                messages.append(StreamMessage(message_id=message_id, fields=fields))
        return messages
