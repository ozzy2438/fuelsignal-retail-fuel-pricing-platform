"""Hashing utilities for data deduplication and audit trails."""

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any


def compute_record_hash(*fields: Any) -> str:
    """Compute a deterministic SHA-256 hash from record fields.

    Used for:
    - Detecting duplicate ingestion of same source records
    - Creating stable record identifiers
    - Change detection between pipeline runs

    Args:
        *fields: Field values to include in hash computation.
                 None values are represented as empty string.

    Returns:
        Hex-encoded SHA-256 hash string.
    """
    # Normalize all fields to string representation
    normalized = "|".join(str(f).strip() if f is not None else "" for f in fields)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def generate_pipeline_run_id(prefix: str = "run") -> str:
    """Generate a unique pipeline run identifier.

    Format: {prefix}_{date}_{uuid4_short}
    Example: run_20260717_a3b4c5d6

    Args:
        prefix: Identifier prefix for the run type.

    Returns:
        Unique run identifier string.
    """
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short_uuid = uuid.uuid4().hex[:8]
    return f"{prefix}_{date_str}_{short_uuid}"
