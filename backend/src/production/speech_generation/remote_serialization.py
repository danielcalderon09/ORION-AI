"""Canonical strict JSON for provider-neutral remote speech records."""

import json
from typing import Any

from backend.src.production.speech_generation.remote_models import (
    RemoteSpeechJobRecord,
)


def serialize_remote_speech_job(record: RemoteSpeechJobRecord) -> bytes:
    return (
        json.dumps(
            record.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def deserialize_remote_speech_job(content: bytes) -> RemoteSpeechJobRecord:
    payload = json.loads(
        content.decode("utf-8", errors="strict"),
        parse_constant=_reject_constant,
        object_pairs_hook=_reject_duplicate_keys,
    )
    return RemoteSpeechJobRecord.model_validate(payload)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result
