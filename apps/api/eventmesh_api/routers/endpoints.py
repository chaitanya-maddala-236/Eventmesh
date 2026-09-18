import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eventmesh_api.auth_deps import AuthContext, require_auth
from eventmesh_api.schemas import (
    EndpointCreateRequest, EndpointCreateResponse, EndpointDetail,
    EndpointUpdateRequest,
)
from eventmesh_common.ssrf import SSRFValidationError, validate_webhook_url
from eventmesh_common.enums import EndpointStatus
from eventmesh_config.settings import get_settings
from eventmesh_database.models import Endpoint, EndpointSubscription

router = APIRouter(prefix="/v1/endpoints", tags=["endpoints"])
settings = get_settings()


def _encrypt_secret(raw: str) -> str:
    # v1: XOR-with-key placeholder replaced by a real AEAD (e.g. Fernet/
    # AES-GCM) before production use — flagged explicitly in
    # docs/security/threat-model.md rather than silently shipped as if
    # it were production-grade encryption at rest.
    from cryptography.fernet import Fernet
    import base64
    import hashlib

    key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_encryption_key.encode()).digest())
    return Fernet(key).encrypt(raw.encode()).decode()


def decrypt_secret(token: str) -> str:
    from cryptography.fernet import Fernet
    import base64
    import hashlib

    key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_encryption_key.encode()).digest())
    return Fernet(key).decrypt(token.encode()).decode()


async def _to_detail(session: AsyncSession, ep: Endpoint) -> EndpointDetail:
    result = await session.execute(select(EndpointSubscription.event_type).where(EndpointSubscription.endpoint_id == ep.id))
    events = [row[0] for row in result.all()]
    return EndpointDetail(
        id=ep.id, url=ep.url, description=ep.description, status=ep.status,
        circuit_state=ep.circuit_state, timeout_ms=ep.timeout_ms,
        max_retries=ep.max_retries, events=events, created_at=ep.created_at,
    )


@router.post("", response_model=EndpointCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_endpoint(body: EndpointCreateRequest, request: Request, ctx: AuthContext = Depends(require_auth)):
    url_str = str(body.url)
    try:
        validate_webhook_url(url_str, max_length=settings.max_endpoint_url_length)
    except SSRFValidationError as e:
        raise HTTPException(400, detail={"error": {"code": "INVALID_URL", "message": str(e)}}) from e

    raw_secret = "whsec_" + secrets.token_urlsafe(32)

    async with request.app.state.session_factory() as session:
        ep = Endpoint(
            tenant_id=ctx.tenant_id, url=url_str, description=body.description,
            secret_encrypted=_encrypt_secret(raw_secret),
            status=EndpointStatus.ACTIVE.value,
            timeout_ms=body.timeout_ms, max_retries=body.max_retries,
        )
        session.add(ep)
        await session.flush()
        for event_type in body.events:
            session.add(EndpointSubscription(endpoint_id=ep.id, event_type=event_type))
        await session.commit()
        detail = await _to_detail(session, ep)

    return EndpointCreateResponse(**detail.model_dump(), signing_secret=raw_secret)


@router.get("", response_model=list[EndpointDetail])
async def list_endpoints(request: Request, ctx: AuthContext = Depends(require_auth)):
    async with request.app.state.session_factory() as session:
        result = await session.execute(select(Endpoint).where(Endpoint.tenant_id == ctx.tenant_id))
        endpoints = result.scalars().all()
        return [await _to_detail(session, ep) for ep in endpoints]


@router.get("/{endpoint_id}", response_model=EndpointDetail)
async def get_endpoint(endpoint_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(require_auth)):
    async with request.app.state.session_factory() as session:
        ep = await session.get(Endpoint, endpoint_id)
        if ep is None or ep.tenant_id != ctx.tenant_id:
            raise HTTPException(404, detail={"error": {"code": "NOT_FOUND", "message": "endpoint not found"}})
        return await _to_detail(session, ep)


@router.patch("/{endpoint_id}", response_model=EndpointDetail)
async def update_endpoint(endpoint_id: uuid.UUID, body: EndpointUpdateRequest, request: Request, ctx: AuthContext = Depends(require_auth)):
    async with request.app.state.session_factory() as session:
        ep = await session.get(Endpoint, endpoint_id)
        if ep is None or ep.tenant_id != ctx.tenant_id:
            raise HTTPException(404, detail={"error": {"code": "NOT_FOUND", "message": "endpoint not found"}})

        if body.url is not None:
            url_str = str(body.url)
            try:
                validate_webhook_url(url_str, max_length=settings.max_endpoint_url_length)
            except SSRFValidationError as e:
                raise HTTPException(400, detail={"error": {"code": "INVALID_URL", "message": str(e)}}) from e
            ep.url = url_str
        if body.description is not None:
            ep.description = body.description
        if body.status is not None:
            ep.status = body.status
        if body.timeout_ms is not None:
            ep.timeout_ms = body.timeout_ms
        if body.max_retries is not None:
            ep.max_retries = body.max_retries
        if body.events is not None:
            await session.execute(
                EndpointSubscription.__table__.delete().where(EndpointSubscription.endpoint_id == ep.id)
            )
            for event_type in body.events:
                session.add(EndpointSubscription(endpoint_id=ep.id, event_type=event_type))

        await session.commit()
        return await _to_detail(session, ep)


@router.delete("/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint(endpoint_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(require_auth)):
    async with request.app.state.session_factory() as session:
        ep = await session.get(Endpoint, endpoint_id)
        if ep is None or ep.tenant_id != ctx.tenant_id:
            raise HTTPException(404, detail={"error": {"code": "NOT_FOUND", "message": "endpoint not found"}})
        await session.delete(ep)
        await session.commit()
