"""Structured JSON logging with correlation ID, lounge ID, and sensitive-field redaction (REQ-077)."""

from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import sys
from typing import Any, Dict
import uuid

correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="")
lounge_id_ctx: ContextVar[str] = ContextVar("lounge_id", default="")

SENSITIVE_KEYS = {
    "password",
    "passwordhash",
    "password_hash",
    "token",
    "accesstoken",
    "access_token",
    "refreshtoken",
    "refresh_token",
    "secret",
    "jwt_secret_key",
    "jwt_refresh_secret_key",
    "api_key",
    "llm_api_key",
    "authorization",
}


def set_log_context(correlation_id: str | None = None, lounge_id: str | None = None) -> str:
    """Bind correlation_id and optional lounge_id to the current async context."""
    cid = correlation_id or correlation_id_ctx.get() or f"corr_{uuid.uuid4().hex[:12]}"
    correlation_id_ctx.set(cid)
    if lounge_id is not None:
        lounge_id_ctx.set(lounge_id)
    return cid


def clear_log_context() -> None:
    """Reset correlation_id and lounge_id context variables."""
    correlation_id_ctx.set("")
    lounge_id_ctx.set("")


def get_correlation_id() -> str:
    """Return active correlation ID or generate a fallback."""
    cid = correlation_id_ctx.get()
    if not cid:
        cid = f"corr_{uuid.uuid4().hex[:12]}"
        correlation_id_ctx.set(cid)
    return cid


def get_lounge_id() -> str | None:
    """Return active lounge ID if bound in context."""
    lid = lounge_id_ctx.get()
    return lid if lid else None


def sanitize_log_payload(data: Any) -> Any:
    """Recursively redact sensitive fields from structured log payloads (REQ-077)."""
    if isinstance(data, dict):
        sanitized: Dict[str, Any] = {}
        for k, v in data.items():
            if str(k).lower().replace("-", "_") in SENSITIVE_KEYS:
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = sanitize_log_payload(v)
        return sanitized
    if isinstance(data, list):
        return [sanitize_log_payload(item) for item in data]
    return data


class StructuredJsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects with correlationId and loungeId."""

    def format(self, record: logging.LogRecord) -> str:
        correlation_id = getattr(record, "correlation_id", None) or correlation_id_ctx.get() or None
        lounge_id = getattr(record, "lounge_id", None) or lounge_id_ctx.get() or None

        log_entry: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlationId": correlation_id,
            "loungeId": lounge_id,
        }

        event_name = getattr(record, "event", None)
        if event_name:
            log_entry["event"] = event_name

        extra_payload = getattr(record, "payload", None)
        if extra_payload is not None:
            log_entry["payload"] = sanitize_log_payload(extra_payload)

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


def configure_logging(log_level: str = "INFO") -> logging.Logger:
    """Configure application-wide structured JSON logging."""
    logger = logging.getLogger("aidj_studio")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    logger.propagate = False

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredJsonFormatter())
        logger.addHandler(handler)
    else:
        for handler in logger.handlers:
            handler.setFormatter(StructuredJsonFormatter())

    return logger


def get_logger(name: str = "aidj_studio") -> logging.Logger:
    """Return a child logger under the aidj_studio namespace."""
    if name == "aidj_studio" or name.startswith("aidj_studio."):
        return logging.getLogger(name)
    return logging.getLogger(f"aidj_studio.{name}")
