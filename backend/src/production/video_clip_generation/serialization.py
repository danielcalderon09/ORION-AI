"""Canonical strict JSON for video clip manifests and sidecars."""

import json
from typing import Any

from backend.src.production.video_clip_generation.models import (
    ProductionVideoClipAsset,
    ProductionVideoClipManifest,
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


def serialize_video_clip_manifest(manifest: ProductionVideoClipManifest) -> bytes:
    return _serialize(manifest.model_dump(mode="json"))


def deserialize_video_clip_manifest(content: bytes) -> ProductionVideoClipManifest:
    return ProductionVideoClipManifest.model_validate(_deserialize(content))


def serialize_video_clip_asset(asset: ProductionVideoClipAsset) -> bytes:
    return _serialize(asset.model_dump(mode="json"))


def deserialize_video_clip_asset(content: bytes) -> ProductionVideoClipAsset:
    return ProductionVideoClipAsset.model_validate(_deserialize(content))


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
