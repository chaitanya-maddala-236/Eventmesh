"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-18

Written by hand rather than via `alembic revision --autogenerate`
because generating this migration requires connecting to a live
PostgreSQL instance, which this environment's sandboxed network does
not have access to. The schema here is a direct 1:1 mapping of
packages/database/eventmesh_database/models.py — reviewers should treat
the ORM models as the source of truth and diff this file against them.
Before running in a real environment, run
`alembic check` (or a fresh autogenerate against an empty DB) to confirm
there is no drift.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("plan", sa.String(50), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("key_prefix", sa.String(32), nullable=False),
        sa.Column("key_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_keys_tenant_id", "api_keys", ["tenant_id"])
    op.create_index("ix_api_keys_key_prefix", "api_keys", ["key_prefix"])

    op.create_table(
        "endpoints",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("secret_encrypted", sa.Text, nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("timeout_ms", sa.Integer, nullable=False),
        sa.Column("max_retries", sa.Integer, nullable=False),
        sa.Column("circuit_state", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_endpoints_tenant_id", "endpoints", ["tenant_id"])
    op.create_index("ix_endpoints_tenant_status", "endpoints", ["tenant_id", "status"])

    op.create_table(
        "endpoint_subscriptions",
        sa.Column("endpoint_id", pg.UUID(as_uuid=True), sa.ForeignKey("endpoints.id"), primary_key=True),
        sa.Column("event_type", sa.String(255), primary_key=True),
    )

    op.create_table(
        "events",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("event_type", sa.String(255), nullable=False),
        sa.Column("source", sa.String(255), nullable=True),
        sa.Column("entity_id", sa.String(255), nullable=True),
        sa.Column("sequence_number", sa.BigInteger, nullable=True),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_events_tenant_id", "events", ["tenant_id"])
    op.create_index("ix_events_tenant_created", "events", ["tenant_id", "created_at"])
    op.create_index("ix_events_tenant_type", "events", ["tenant_id", "event_type"])
    op.create_index("ix_events_entity_seq", "events", ["entity_id", "sequence_number"])
    op.create_index("ix_events_status", "events", ["status"])

    op.create_table(
        "deliveries",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", pg.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("endpoint_id", pg.UUID(as_uuid=True), sa.ForeignKey("endpoints.id"), nullable=False),
        sa.Column("attempt_number", sa.Integer, nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("http_status", sa.Integer, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_replay", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_deliveries_event_id", "deliveries", ["event_id"])
    op.create_index("ix_deliveries_endpoint_id", "deliveries", ["endpoint_id"])
    op.create_index("ix_deliveries_status", "deliveries", ["status"])
    op.create_index("ix_deliveries_next_retry_at", "deliveries", ["next_retry_at"])

    op.create_table(
        "idempotency_keys",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("event_id", pg.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "key", name="uq_idempotency_tenant_key"),
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("aggregate_type", sa.String(100), nullable=False),
        sa.Column("aggregate_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", pg.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_outbox_status_created", "outbox_events", ["status", "created_at"])


def downgrade() -> None:
    op.drop_table("outbox_events")
    op.drop_table("idempotency_keys")
    op.drop_table("deliveries")
    op.drop_table("events")
    op.drop_table("endpoint_subscriptions")
    op.drop_table("endpoints")
    op.drop_table("api_keys")
    op.drop_table("tenants")
