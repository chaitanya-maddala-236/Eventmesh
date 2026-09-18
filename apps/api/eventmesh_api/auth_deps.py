import time
import uuid
from dataclasses import dataclass

import redis.asyncio as redis
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eventmesh_auth.signature import hash_api_key
from eventmesh_database.models import ApiKey, Tenant
from eventmesh_common.enums import TenantPlan


@dataclass
class AuthContext:
    tenant_id: uuid.UUID
    api_key_id: uuid.UUID
    plan: str


RATE_LIMITS = {
    TenantPlan.FREE.value: 100,
    TenantPlan.PRO.value: 1000,
    TenantPlan.ENTERPRISE.value: 10000,
}


def _unauthorized(msg: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": {"code": "UNAUTHORIZED", "message": msg}},
    )


async def get_auth_context(request: Request) -> AuthContext:
    authz = request.headers.get("authorization", "")
    if not authz.startswith("Bearer "):
        raise _unauthorized("missing or malformed Authorization header")
    raw_key = authz.removeprefix("Bearer ").strip()
    if not raw_key:
        raise _unauthorized("empty API key")

    key_hash = hash_api_key(raw_key)
    session_factory = request.app.state.session_factory

    async with session_factory() as session:  # type: AsyncSession
        result = await session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        api_key = result.scalar_one_or_none()
        if api_key is None or api_key.revoked_at is not None:
            raise _unauthorized("invalid API key")

        tenant = await session.get(Tenant, api_key.tenant_id)
        if tenant is None or tenant.status != "active":
            raise _unauthorized("tenant is not active")

        api_key.last_used_at = __import__("datetime").datetime.utcnow()
        await session.commit()

    ctx = AuthContext(tenant_id=tenant.id, api_key_id=api_key.id, plan=tenant.plan)
    await _enforce_rate_limit(request, ctx)
    return ctx


async def _enforce_rate_limit(request: Request, ctx: AuthContext) -> None:
    """
    Fixed-window per-minute counter in Redis (PRD §41). Fixed windows can
    allow up to 2x burst at window boundaries versus a sliding window —
    documented trade-off, acceptable for v1 (see ADR-011 in
    docs/decisions), simple and O(1) per request.
    """
    redis_client: redis.Redis = request.app.state.redis
    limit = RATE_LIMITS.get(ctx.plan, RATE_LIMITS[TenantPlan.FREE.value])
    window = int(time.time() // 60)
    key = f"ratelimit:{ctx.tenant_id}:{window}"

    current = await redis_client.incr(key)
    if current == 1:
        await redis_client.expire(key, 90)  # generous TTL past window end

    if current > limit:
        from eventmesh_metrics.registry import rate_limit_rejections_total
        rate_limit_rejections_total.labels(scope="tenant").inc()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": {"code": "RATE_LIMITED", "message": "rate limit exceeded"}},
            headers={"Retry-After": "60"},
        )


def require_auth(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    return ctx
