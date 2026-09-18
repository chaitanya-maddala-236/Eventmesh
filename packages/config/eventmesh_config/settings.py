"""
Centralized configuration. PRD §89: nothing here has a hard-coded secret;
every value is overridable via environment variable and everything has a
safe *local-dev-only* default so `docker compose up` works out of the box
from .env.example.
"""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Core services -----------------------------------------------
    database_url: str = Field(default="postgresql+asyncpg://eventmesh:eventmesh@postgres:5432/eventmesh")
    redis_url: str = Field(default="redis://redis:6379/0")

    # --- Security ------------------------------------------------------
    # Symmetric key used to encrypt webhook secrets at rest (PRD §61).
    # In production this MUST be provided via a secret manager, not committed.
    secret_encryption_key: str = Field(default="local-dev-only-change-me-32bytes!!")
    api_key_min_rotation_days: int = 90

    # --- Timeouts (PRD §64) -------------------------------------------
    http_connect_timeout_seconds: float = 2.0
    http_read_timeout_seconds: float = 5.0
    http_write_timeout_seconds: float = 5.0
    http_total_timeout_seconds: float = 10.0
    max_redirects: int = 0  # PRD §62: never blindly follow redirects

    # --- Payload limits (PRD §63) --------------------------------------
    max_event_payload_bytes: int = 256 * 1024
    max_endpoint_url_length: int = 2048
    max_event_type_length: int = 255

    # --- Retry (PRD §31-32) ---------------------------------------------
    retry_base_seconds: float = 1.0
    retry_max_delay_seconds: float = 32.0
    retry_max_attempts: int = 6
    retry_jitter_ratio: float = 0.2

    # --- Circuit breaker (PRD §42) --------------------------------------
    circuit_failure_threshold: int = 5
    circuit_open_duration_seconds: float = 30.0
    circuit_half_open_max_requests: int = 1

    # --- Worker (PRD §44, §103) -------------------------------------------
    worker_concurrency: int = 20
    worker_consumer_group: str = "eventmesh-delivery-group"
    worker_stream_name: str = "eventmesh:deliveries"
    worker_batch_size: int = 10
    worker_block_ms: int = 5000
    worker_stale_claim_idle_ms: int = 60_000
    worker_graceful_shutdown_seconds: int = 30

    # --- Rate limiting (PRD §41) ------------------------------------------
    rate_limit_free_per_minute: int = 100
    rate_limit_pro_per_minute: int = 1000
    rate_limit_enterprise_per_minute: int = 10000

    # --- Retention (PRD §105) ---------------------------------------------
    retention_events_days: int = 30
    retention_deliveries_days: int = 30
    retention_dlq_days: int = 90
    idempotency_key_ttl_days: int = 7

    # --- Misc ---------------------------------------------------------
    environment: str = "development"
    log_level: str = "INFO"
    service_name: str = "eventmesh"


@lru_cache
def get_settings() -> Settings:
    return Settings()
