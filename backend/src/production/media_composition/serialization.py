"""Canonical strict JSON with identity verification."""

import json
from typing import Any

from backend.src.production.media_composition.domain.fingerprints import (
    canonical_json_bytes,
    canonical_sha256,
)
from backend.src.production.media_composition.domain.models import (
    MediaCompositionManifest,
    MediaCompositionPlan,
)
from backend.src.production.media_composition.exceptions import (
    MediaCompositionCorruptError,
)


def serialize_media_composition_plan(plan: MediaCompositionPlan) -> bytes:
    _validate_plan_identities(plan)
    return canonical_json_bytes(plan.model_dump(mode="json"))


def deserialize_media_composition_plan(content: bytes) -> MediaCompositionPlan:
    try:
        payload = _deserialize(content)
        _validate_serialized_plan_identities(payload)
        return MediaCompositionPlan.model_validate(payload)
    except MediaCompositionCorruptError:
        raise
    except (TypeError, ValueError, UnicodeError) as exc:
        raise MediaCompositionCorruptError("media composition plan is invalid") from exc


def serialize_media_composition_manifest(
    manifest: MediaCompositionManifest,
) -> bytes:
    return canonical_json_bytes(manifest.model_dump(mode="json"))


def deserialize_media_composition_manifest(
    content: bytes,
) -> MediaCompositionManifest:
    try:
        return MediaCompositionManifest.model_validate(_deserialize(content))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise MediaCompositionCorruptError("media composition manifest is invalid") from exc


def _validate_plan_identities(plan: MediaCompositionPlan) -> None:
    source_payload = {
        "assets": [item.model_dump(mode="json") for item in plan.assets],
        "manifests": [item.model_dump(mode="json") for item in plan.source_manifests],
    }
    timeline_payload = {
        "ducking": [item.model_dump(mode="json") for item in plan.ducking],
        "output": plan.output.model_dump(mode="json"),
        "subtitle_cues": [item.model_dump(mode="json") for item in plan.subtitle_cues],
        "tracks": [item.model_dump(mode="json") for item in plan.tracks],
        "transitions": [item.model_dump(mode="json") for item in plan.transitions],
    }
    if canonical_sha256(source_payload) != plan.source_fingerprint:
        raise MediaCompositionCorruptError("composition source fingerprint differs")
    if canonical_sha256(timeline_payload) != plan.timeline_checksum:
        raise MediaCompositionCorruptError("composition timeline checksum differs")
    identity = plan.model_dump(mode="json", exclude={"plan_fingerprint"})
    if canonical_sha256(identity) != plan.plan_fingerprint:
        raise MediaCompositionCorruptError("composition plan fingerprint differs")


def _validate_serialized_plan_identities(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise MediaCompositionCorruptError("media composition plan is invalid")
    try:
        source_payload = {
            "assets": payload["assets"],
            "manifests": payload["source_manifests"],
        }
        timeline_payload = {
            "ducking": payload["ducking"],
            "output": payload["output"],
            "subtitle_cues": payload["subtitle_cues"],
            "tracks": payload["tracks"],
            "transitions": payload["transitions"],
        }
        source_fingerprint = payload["source_fingerprint"]
        timeline_checksum = payload["timeline_checksum"]
        plan_fingerprint = payload["plan_fingerprint"]
    except KeyError as exc:
        raise MediaCompositionCorruptError("media composition plan is invalid") from exc
    if canonical_sha256(source_payload) != source_fingerprint:
        raise MediaCompositionCorruptError("composition source fingerprint differs")
    if canonical_sha256(timeline_payload) != timeline_checksum:
        raise MediaCompositionCorruptError("composition timeline checksum differs")
    identity = {key: value for key, value in payload.items() if key != "plan_fingerprint"}
    if canonical_sha256(identity) != plan_fingerprint:
        raise MediaCompositionCorruptError("composition plan fingerprint differs")


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
