"""
Structured JSON logging (PRD §50).

Every log line is JSON with a consistent envelope. Callers pass
correlation fields (event_id, endpoint_id, request_id, attempt, ...) as
kwargs to logger.bind(...) / log calls — never string-format them into
the message, so they stay queryable.

Denylist below enforces PRD §50: never log API keys, webhook secrets,
full Authorization headers, or raw payload bytes by default.
"""
import logging
import sys

import structlog

_REDACT_KEYS = {"api_key", "authorization", "secret", "webhook_secret", "key_hash", "password", "token"}


def _redact_processor(_logger, _method_name, event_dict):
    for k in list(event_dict.keys()):
        if k.lower() in _REDACT_KEYS:
            event_dict[k] = "***REDACTED***"
    return event_dict


def configure_logging(service_name: str, level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service_name)


def get_logger(**initial_context):
    return structlog.get_logger(**initial_context)
