"""Strict canonical serialization for OpenRouter scripting request records."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from backend.src.production.scripting.openrouter_request import (
    OpenRouterScriptingRequestRecord,
)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def serialize_openrouter_scripting_request(
    record: OpenRouterScriptingRequestRecord,
) -> bytes:
    return json.dumps(
        record.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def deserialize_openrouter_scripting_request(
    content: bytes,
) -> OpenRouterScriptingRequestRecord:
    payload = json.loads(
        content.decode("utf-8", errors="strict"),
        parse_constant=_reject_constant,
        parse_float=Decimal,
        object_pairs_hook=_reject_duplicate_keys,
    )
    if not isinstance(payload, dict):
        raise ValueError("OpenRouter scripting request must be an object")
    return OpenRouterScriptingRequestRecord.model_validate(payload)


__all__ = [
    "deserialize_openrouter_scripting_request",
    "serialize_openrouter_scripting_request",
]
