"""Canonical strict JSON serialization for image acquisition manifests."""

import json
from typing import Any

from backend.src.production.image_acquisition.models import (
    ProductionImageAcquisitionManifest,
)


def serialize_image_acquisition_manifest(
    manifest: ProductionImageAcquisitionManifest,
) -> bytes:
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


def deserialize_image_acquisition_manifest(
    content: bytes,
) -> ProductionImageAcquisitionManifest:
    payload = json.loads(
        content.decode("utf-8", errors="strict"),
        parse_constant=_reject_constant,
        object_pairs_hook=_reject_duplicate_keys,
    )
    return ProductionImageAcquisitionManifest.model_validate(payload)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result
