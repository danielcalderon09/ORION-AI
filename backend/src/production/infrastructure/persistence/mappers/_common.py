"""Shared strict conversion helpers for persistence mappers."""

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from backend.src.production.infrastructure.persistence.exceptions import (
    ProductionRecordIntegrityError,
)


def canonical_uuid(value: UUID) -> str:
    return str(value)


def parse_uuid(value: str, *, field_name: str) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ProductionRecordIntegrityError(f"invalid UUID in {field_name}: {value!r}") from exc


def require_aware(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProductionRecordIntegrityError(f"{field_name} must be timezone-aware")
    return value


def json_value(value: Any, *, field_name: str) -> Any:
    """Return a detached JSON value or reject non-portable content."""

    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
        return json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ProductionRecordIntegrityError(f"{field_name} is not valid JSON") from exc
