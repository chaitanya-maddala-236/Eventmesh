import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eventmesh_api.auth_deps import AuthContext, require_auth
from eventmesh_api.schemas import EventCreateRequest, EventCreateResponse, EventDetail
from eventmesh_common.enums import EventStatus, OutboxStatus
from eventmesh_config.settings import get_settings
from eventmesh_database.models import Event, IdempotencyKey, OutboxEvent
from eventmesh_metrics.registry import events_failed_total, events_ingested_total

router = APIRouter(prefix="/v1/events", tags=["events"])
settings = get_settings()


async def get_session(request: Request) -> AsyncSession:
    async with request.app.state.session_factory() as session:
        yield session


@router.post("", response_model=EventCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_event(
    body: EventCreateRequest,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    idempotency_key: str | None = None,
):
    idempotency_key = request.headers.get("Idempotency-Key")
    raw_body_len = int(request.headers.get("content-length") or 0)
    if raw_body_len and raw_body_len > settings.max_event_payload_bytes:
        events_failed_total.labels(reason="payload_too_large").inc()
        raise HTTPException(413, detail={"error": {"code": "PAYLOAD_TOO_LARGE", "message": "event payload exceeds limit"}})

    session_factory = request.app.state.session_factory
    async with session_factory() as session:  # type: AsyncSession
        # --- Idempotency check (PRD §36-37) -----------------------------
        if idempotency_key:
            existing = await session.execute(
                select(IdempotencyKey).where(
                    IdempotencyKey.tenant_id == ctx.tenant_id,
                    IdempotencyKey.key == idempotency_key,
                )
            )
            row = existing.scalar_one_or_none()
            if row is not None:
                return EventCreateResponse(event_id=row.event_id, status="queued")

        # --- Durable persistence BEFORE the response (PRD §25) ----------
        event = Event(
            id=uuid.uuid4(),
            tenant_id=ctx.tenant_id,
            event_type=body.type,
            source=body.source,
            entity_id=body.entity_id,
            sequence_number=body.sequence,
            payload=body.data,
            status=EventStatus.RECEIVED.value,
        )
        session.add(event)
        await session.flush()  # obtain event.id-backed FK integrity before outbox insert

        # --- Transactional outbox (PRD §66-68): event row + outbox row
        # commit atomically. If the process crashes between commit and
        # the outbox publisher's next poll, the event is still durable
        # and WILL be published — nothing is lost, at worst delivery is
        # delayed by one publisher poll interval.
        outbox = OutboxEvent(
            aggregate_type="event",
            aggregate_id=event.id,
            event_id=event.id,
            payload={
                "event_id": str(event.id),
                "tenant_id": str(ctx.tenant_id),
                "type": event.event_type,
                "entity_id": event.entity_id,
                "sequence": event.sequence_number,
                "data": event.payload,
                "created_at": datetime.utcnow().isoformat(),
            },
            status=OutboxStatus.PENDING.value,
        )
        session.add(outbox)

        if idempotency_key:
            session.add(IdempotencyKey(
                tenant_id=ctx.tenant_id,
                key=idempotency_key,
                event_id=event.id,
                expires_at=datetime.utcnow() + timedelta(days=settings.idempotency_key_ttl_days),
            ))

        await session.commit()

    events_ingested_total.labels(event_type=body.type).inc()
    return EventCreateResponse(event_id=event.id, status="queued")


@router.get("/{event_id}", response_model=EventDetail)
async def get_event(event_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(require_auth)):
    async with request.app.state.session_factory() as session:
        event = await session.get(Event, event_id)
        if event is None or event.tenant_id != ctx.tenant_id:
            raise HTTPException(404, detail={"error": {"code": "NOT_FOUND", "message": "event not found"}})
        return EventDetail.model_validate(event)


@router.get("", response_model=list[EventDetail])
async def list_events(
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    limit: int = 50,
    cursor: str | None = None,
    event_type: str | None = None,
):
    limit = min(max(limit, 1), 200)
    async with request.app.state.session_factory() as session:
        stmt = select(Event).where(Event.tenant_id == ctx.tenant_id).order_by(Event.created_at.desc(), Event.id.desc())
        if event_type:
            stmt = stmt.where(Event.event_type == event_type)
        if cursor:
            # Cursor = base64-free simple "<iso_ts>|<uuid>" pair (PRD §47:
            # never return unlimited results; cursor pagination only).
            try:
                ts_str, id_str = cursor.split("|", 1)
                cursor_ts = datetime.fromisoformat(ts_str)
                cursor_id = uuid.UUID(id_str)
            except (ValueError, TypeError) as e:
                raise HTTPException(400, detail={"error": {"code": "INVALID_CURSOR", "message": str(e)}}) from e
            stmt = stmt.where(
                (Event.created_at < cursor_ts)
                | ((Event.created_at == cursor_ts) & (Event.id < cursor_id))
            )
        stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        events = result.scalars().all()
        return [EventDetail.model_validate(e) for e in events]


@router.post("/{event_id}/replay", response_model=EventCreateResponse)
async def replay_event(event_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(require_auth)):
    """
    PRD §35: preserve the original event_id, re-publish an outbox row
    (marked as a replay) so it re-enters the delivery pipeline without
    creating a new logical event.
    """
    async with request.app.state.session_factory() as session:
        event = await session.get(Event, event_id)
        if event is None or event.tenant_id != ctx.tenant_id:
            raise HTTPException(404, detail={"error": {"code": "NOT_FOUND", "message": "event not found"}})

        outbox = OutboxEvent(
            aggregate_type="event_replay",
            aggregate_id=event.id,
            event_id=event.id,
            payload={
                "event_id": str(event.id),
                "tenant_id": str(ctx.tenant_id),
                "type": event.event_type,
                "entity_id": event.entity_id,
                "sequence": event.sequence_number,
                "data": event.payload,
                "created_at": datetime.utcnow().isoformat(),
                "is_replay": True,
            },
            status=OutboxStatus.PENDING.value,
        )
        session.add(outbox)
        event.status = EventStatus.QUEUED.value
        await session.commit()

    return EventCreateResponse(event_id=event.id, status="queued")
