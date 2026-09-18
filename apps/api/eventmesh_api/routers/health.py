from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

router = APIRouter(tags=["health"])


@router.get("/v1/health")
async def health():
    """Liveness: is this process alive? Deliberately checks NOTHING
    external — PRD §74 says liveness must not depend on PostgreSQL (or
    Redis). A dependency outage should not cause the orchestrator to
    kill and restart otherwise-healthy API processes."""
    return {"status": "ok"}


@router.get("/v1/ready")
async def ready(request: Request, response: Response):
    """Readiness: are required dependencies reachable?"""
    checks = {}
    healthy = True

    try:
        async with request.app.state.session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as e:  # noqa: BLE001 — readiness check intentionally broad
        checks["postgres"] = f"error: {e}"
        healthy = False

    try:
        await request.app.state.redis.ping()
        checks["redis"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["redis"] = f"error: {e}"
        healthy = False

    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if healthy else "degraded", "checks": checks}
