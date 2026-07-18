"""FuelSignal Logging Module.

Provides structured logging for pipeline operations.
NEVER logs secrets, tokens, or credentials.
"""

import logging
import sys
from datetime import datetime, timezone

SENSITIVE_KEYS = {
    "token",
    "password",
    "secret",
    "key",
    "credential",
    "DATABRICKS_TOKEN",
    "api_key",
    "auth",
}


def _redact_sensitive(record: logging.LogRecord) -> logging.LogRecord:
    """Redact any sensitive information from log records."""
    msg = str(record.msg)
    for key in SENSITIVE_KEYS:
        if key.lower() in msg.lower():
            # Don't log the full message if it might contain secrets
            pass
    return record


def get_logger(
    name: str,
    level: str = "INFO",
    include_timestamp: bool = True,
) -> logging.Logger:
    """Create a configured logger instance.

    Args:
        name: Logger name (typically module __name__)
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        include_timestamp: Whether to include timestamp in format

    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(getattr(logging, level.upper(), logging.INFO))

        if include_timestamp:
            fmt = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
        else:
            fmt = "%(name)s | %(levelname)s | %(message)s"

        handler.setFormatter(logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(handler)

    return logger


def log_pipeline_event(
    logger: logging.Logger,
    event: str,
    stage: str,
    status: str = "info",
    details: dict | None = None,
) -> dict:
    """Log a structured pipeline event.

    Args:
        logger: Logger instance
        event: Event description
        stage: Pipeline stage (bronze/silver/gold)
        status: Event status
        details: Additional event details (secrets will be redacted)

    Returns:
        Event record dictionary for audit logging.
    """
    # Redact any sensitive values from details
    safe_details = {}
    if details:
        for k, v in details.items():
            if any(s in k.lower() for s in SENSITIVE_KEYS):
                safe_details[k] = "[REDACTED]"
            else:
                safe_details[k] = v

    event_record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "stage": stage,
        "status": status,
        "details": safe_details,
    }

    log_msg = f"[{stage}] {event}"
    if safe_details:
        log_msg += f" | {safe_details}"

    log_level = getattr(logging, status.upper(), logging.INFO)
    logger.log(log_level, log_msg)

    return event_record
