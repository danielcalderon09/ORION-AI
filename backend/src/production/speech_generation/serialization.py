"""Canonical strict JSON for speech manifests and asset sidecars."""

import json
from typing import Any

from backend.src.production.speech_generation.models import (
    SpeechBinaryAsset,
    SpeechGenerationManifest,
)


def _serialize(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def serialize_speech_manifest(manifest: SpeechGenerationManifest) -> bytes:
    return _serialize(manifest.model_dump(mode="json"))


def deserialize_speech_manifest(content: bytes) -> SpeechGenerationManifest:
    return SpeechGenerationManifest.model_validate(_deserialize(content))


def serialize_speech_asset(asset: SpeechBinaryAsset) -> bytes:
    return _serialize(asset.model_dump(mode="json"))


def deserialize_speech_asset(content: bytes) -> SpeechBinaryAsset:
    return SpeechBinaryAsset.model_validate(_deserialize(content))


def _deserialize(content: bytes) -> Any:
    return json.loads(
        content.decode("utf-8", errors="strict"),
        parse_constant=_reject_constant,
        object_pairs_hook=_reject_duplicate_keys,
    )


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result
