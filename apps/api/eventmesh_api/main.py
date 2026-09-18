import uuid
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.exceptions import HTTPException as StarletteHTTPException

from eventmesh_api.routers import deliveries, endpoints, events, health
from eventmesh_config.settings import get_settings
from eventmesh_database.session import make_engine, make_session_factory
from eventmesh_logging.setup import configure_logging, get_logger

settings = get_settings()
configure_logging(settings.service_name, settings.log_level)
logger = get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = make_engine(settings.database_url)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.redis = redis.from_url(settings.redis_url, decode_responses=False)
    logger.info("api_startup", database_url_host=settings.database_url.split("@")[-1])
    yield
    await app.state.redis.aclose()
    await engine.dispose()
    logger.info("api_shutdown")


app = FastAPI(
    title="EventMesh API",
    description="Reliable, multi-tenant event & webhook delivery platform.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(events.router)
app.include_router(endpoints.router)
app.include_router(deliveries.router)
app.include_router(health.router)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    # PRD §49: every request gets a request ID; if the client supplied
    # one, validate it before propagating rather than trusting it blindly.
    incoming = request.headers.get("X-Request-ID")
    if incoming and 1 <= len(incoming) <= 128 and incoming.isascii():
        request_id = incoming
    else:
        request_id = f"req_{uuid.uuid4().hex[:20]}"
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        detail["error"]["request_id"] = getattr(request.state, "request_id", None)
        return JSONResponse(status_code=exc.status_code, content=detail, headers=getattr(exc, "headers", None))
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": "ERROR", "message": str(detail), "request_id": getattr(request.state, "request_id", None)}},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "INVALID_REQUEST",
                "message": "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()),
                "request_id": getattr(request.state, "request_id", None),
            }
        },
    )
    # Note: intentionally never includes exc.body or stack traces (PRD §48).


@app.get("/metrics")
async def metrics():
    from starlette.responses import Response as StarletteResponse
    return StarletteResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)
