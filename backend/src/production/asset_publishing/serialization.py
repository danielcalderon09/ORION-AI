"""Canonical strict JSON for published asset manifests."""

import json
from typing import Any

from backend.src.production.asset_publishing.exceptions import (
    PublishedAssetManifestCorruptError,
)
from backend.src.production.asset_publishing.models import PublishedAssetManifest


def serialize_published_asset_manifest(manifest: PublishedAssetManifest) -> bytes:
    return (
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def deserialize_published_asset_manifest(content: bytes) -> PublishedAssetManifest:
    try:
        payload = json.loads(
            content.decode("utf-8", errors="strict"),
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicates,
        )
        return PublishedAssetManifest.model_validate(payload)
    except PublishedAssetManifestCorruptError:
        raise
    except (UnicodeError, ValueError, TypeError) as exc:
        raise PublishedAssetManifestCorruptError(
            "published manifest JSON is invalid"
        ) from exc


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result
