"""Canonical strict JSON for audio-design manifests."""

import json
from typing import Any

from backend.src.production.audio_design.models import (
    AudioDesignManifest,
    StoredAudioDesignAsset,
)


def serialize_audio_design_manifest(manifest: AudioDesignManifest) -> bytes:
    return (
        json.dumps(
            manifest.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def deserialize_audio_design_manifest(content: bytes) -> AudioDesignManifest:
    return AudioDesignManifest.model_validate(
        json.loads(
            content.decode("utf-8", errors="strict"),
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    )


def serialize_audio_design_asset(asset: StoredAudioDesignAsset) -> bytes:
    return _serialize(asset.model_dump(mode="json"))


def deserialize_audio_design_asset(content: bytes) -> StoredAudioDesignAsset:
    return StoredAudioDesignAsset.model_validate(_deserialize(content))


def _serialize(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


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
