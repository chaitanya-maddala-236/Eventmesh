"""
SQLAlchemy 2.x ORM models — schema matches PRD §26 exactly, plus the
outbox (PRD §67) and idempotency (PRD §37) tables described elsewhere in
the PRD.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from eventmesh_common.enums import (
    CircuitState, DeliveryStatus, EndpointStatus, EventStatus, OutboxStatus,
    TenantPlan, TenantStatus,
)


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(String(50), nullable=False, default=TenantPlan.FREE.value)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default=TenantStatus.ACTIVE.value)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)

    api_keys: Mapped[list["ApiKey"]] = relationship(back_populates="tenant")
    endpoints: Mapped[list["Endpoint"]] = relationship(back_populates="tenant")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False, default="member")
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)

    tenant: Mapped["Tenant"] = relationship(back_populates="api_keys")


class Endpoint(Base):
    __tablename__ = "endpoints"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default=EndpointStatus.ACTIVE.value)
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=10_000)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    circuit_state: Mapped[str] = mapped_column(String(20), nullable=False, default=CircuitState.CLOSED.value)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)

    tenant: Mapped["Tenant"] = relationship(back_populates="endpoints")
    subscriptions: Mapped[list["EndpointSubscription"]] = relationship(back_populates="endpoint", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_endpoints_tenant_status", "tenant_id", "status"),)


class EndpointSubscription(Base):
    __tablename__ = "endpoint_subscriptions"

    endpoint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("endpoints.id"), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(255), primary_key=True)

    endpoint: Mapped["Endpoint"] = relationship(back_populates="subscriptions")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sequence_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default=EventStatus.RECEIVED.value)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_events_tenant_id", "tenant_id"),
        Index("ix_events_tenant_created", "tenant_id", "created_at"),
        Index("ix_events_tenant_type", "tenant_id", "event_type"),
        Index("ix_events_entity_seq", "entity_id", "sequence_number"),
        Index("ix_events_status", "status"),
    )


class Delivery(Base):
    __tablename__ = "deliveries"

    id: Mapped[uuid.UUID] = _uuid_pk()
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False)
    endpoint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("endpoints.id"), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default=DeliveryStatus.PENDING.value)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(nullable=True)
    is_replay: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_deliveries_event_id", "event_id"),
        Index("ix_deliveries_endpoint_id", "endpoint_id"),
        Index("ix_deliveries_status", "status"),
        Index("ix_deliveries_next_retry_at", "next_retry_at"),
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_idempotency_tenant_key"),)


class OutboxEvent(Base):
    """Transactional outbox (PRD §66-68). Written in the SAME DB
    transaction as the Event row, so publication is never lost between
    'commit the event' and 'push to Redis'."""
    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False, default="event")
    aggregate_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default=OutboxStatus.PENDING.value)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (Index("ix_outbox_status_created", "status", "created_at"),)
