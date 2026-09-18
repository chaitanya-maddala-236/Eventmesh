import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eventmesh_api.auth_deps import AuthContext, require_auth
from eventmesh_api.schemas import DeliveryDetail, EventCreateResponse
from eventmesh_common.enums import DeliveryStatus, OutboxStatus
from eventmesh_database.models import Delivery, Endpoint, Event, OutboxEvent

router = APIRouter(tags=["deliveries"])


@router.get("/v1/endpoints/{endpoint_id}/deliveries", response_model=list[DeliveryDetail])
async def list_endpoint_deliveries(endpoint_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(require_auth), limit: int = 50):
    limit = min(max(limit, 1), 200)
    async with request.app.state.session_factory() as session:  # type: AsyncSession
        ep = await session.get(Endpoint, endpoint_id)
        if ep is None or ep.tenant_id != ctx.tenant_id:
            raise HTTPException(404, detail={"error": {"code": "NOT_FOUND", "message": "endpoint not found"}})
        result = await session.execute(
            select(Delivery).where(Delivery.endpoint_id == endpoint_id).order_by(Delivery.created_at.desc()).limit(limit)
        )
        return [DeliveryDetail.model_validate(d) for d in result.scalars().all()]


@router.get("/v1/deliveries/{delivery_id}", response_model=DeliveryDetail)
async def get_delivery(delivery_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(require_auth)):
    async with request.app.state.session_factory() as session:
        delivery = await session.get(Delivery, delivery_id)
        if delivery is None:
            raise HTTPException(404, detail={"error": {"code": "NOT_FOUND", "message": "delivery not found"}})
        event = await session.get(Event, delivery.event_id)
        if event is None or event.tenant_id != ctx.tenant_id:
            raise HTTPException(404, detail={"error": {"code": "NOT_FOUND", "message": "delivery not found"}})
        return DeliveryDetail.model_validate(delivery)


@router.get("/v1/dlq", response_model=list[DeliveryDetail])
async def list_dlq(request: Request, ctx: AuthContext = Depends(require_auth), limit: int = 50):
    limit = min(max(limit, 1), 200)
    async with request.app.state.session_factory() as session:
        # Join through Event to scope by tenant (deliveries table has no
        # tenant_id column by design — it's derived via its event).
        result = await session.execute(
            select(Delivery)
            .join(Event, Event.id == Delivery.event_id)
            .where(Event.tenant_id == ctx.tenant_id, Delivery.status == DeliveryStatus.DLQ.value)
            .order_by(Delivery.created_at.desc())
            .limit(limit)
        )
        return [DeliveryDetail.model_validate(d) for d in result.scalars().all()]


@router.post("/v1/dlq/{delivery_id}/replay", response_model=EventCreateResponse)
async def replay_dlq_entry(delivery_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(require_auth)):
    async with request.app.state.session_factory() as session:
        delivery = await session.get(Delivery, delivery_id)
        if delivery is None:
            raise HTTPException(404, detail={"error": {"code": "NOT_FOUND", "message": "DLQ entry not found"}})
        event = await session.get(Event, delivery.event_id)
        if event is None or event.tenant_id != ctx.tenant_id:
            raise HTTPException(404, detail={"error": {"code": "NOT_FOUND", "message": "DLQ entry not found"}})
        if delivery.status != DeliveryStatus.DLQ.value:
            raise HTTPException(409, detail={"error": {"code": "INVALID_STATE", "message": "delivery is not in the DLQ"}})

        outbox = OutboxEvent(
            aggregate_type="dlq_replay",
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
                "target_endpoint_id": str(delivery.endpoint_id),
            },
            status=OutboxStatus.PENDING.value,
        )
        session.add(outbox)
        await session.commit()

    return EventCreateResponse(event_id=event.id, status="queued")
