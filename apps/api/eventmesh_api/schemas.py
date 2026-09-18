import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, field_validator


class EventCreateRequest(BaseModel):
    type: str = Field(..., max_length=255)
    data: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None
    entity_id: str | None = None
    sequence: int | None = None

    @field_validator("type")
    @classmethod
    def type_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("event type is required")
        return v


class EventCreateResponse(BaseModel):
    event_id: uuid.UUID
    status: str


class EventDetail(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    event_type: str
    payload: dict[str, Any]
    status: str
    entity_id: str | None
    sequence_number: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EndpointCreateRequest(BaseModel):
    url: HttpUrl
    description: str | None = None
    events: list[str] = Field(default_factory=list, description="event types to subscribe to")
    timeout_ms: int = 10_000
    max_retries: int = 6


class EndpointUpdateRequest(BaseModel):
    url: HttpUrl | None = None
    description: str | None = None
    events: list[str] | None = None
    status: str | None = None
    timeout_ms: int | None = None
    max_retries: int | None = None


class EndpointDetail(BaseModel):
    id: uuid.UUID
    url: str
    description: str | None
    status: str
    circuit_state: str
    timeout_ms: int
    max_retries: int
    events: list[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class EndpointCreateResponse(EndpointDetail):
    # Raw secret is only ever returned at creation time, mirroring the
    # API-key "show once" behavior in PRD §58.
    signing_secret: str


class DeliveryDetail(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    endpoint_id: uuid.UUID
    attempt_number: int
    status: str
    http_status: int | None
    latency_ms: int | None
    error_message: str | None
    next_retry_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreateResponse(BaseModel):
    id: uuid.UUID
    name: str
    key_prefix: str
    raw_key: str  # shown once


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
