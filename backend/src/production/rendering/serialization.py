"""Strict canonical JSON serialization with identity verification."""

import json
from typing import Any

from backend.src.production.rendering.exceptions import RenderingCorruptError
from backend.src.production.rendering.fingerprints import canonical_json_bytes
from backend.src.production.rendering.models import (
    LOCAL_RENDER_SCHEMA_VERSION,
    LocalRenderRequest,
    RenderExecutionManifest,
)
from backend.src.production.rendering.request_builder import (
    render_request_fingerprint,
)


def serialize_local_render_request(request: LocalRenderRequest) -> bytes:
    _validate_request(request)
    return canonical_json_bytes(request.model_dump(mode="json"))


def deserialize_local_render_request(content: bytes) -> LocalRenderRequest:
    try:
        request = LocalRenderRequest.model_validate(_deserialize(content))
        _validate_request(request)
        return request
    except RenderingCorruptError:
        raise
    except (TypeError, ValueError, UnicodeError) as exc:
        raise RenderingCorruptError("local render request is invalid") from exc


def serialize_render_execution_manifest(
    manifest: RenderExecutionManifest,
) -> bytes:
    if manifest.schema_version != LOCAL_RENDER_SCHEMA_VERSION:
        raise RenderingCorruptError("render execution manifest schema is unsupported")
    return canonical_json_bytes(manifest.model_dump(mode="json"))


def deserialize_render_execution_manifest(
    content: bytes,
) -> RenderExecutionManifest:
    try:
        manifest = RenderExecutionManifest.model_validate(_deserialize(content))
        if manifest.schema_version != LOCAL_RENDER_SCHEMA_VERSION:
            raise RenderingCorruptError("render execution manifest schema is unsupported")
        return manifest
    except RenderingCorruptError:
        raise
    except (TypeError, ValueError, UnicodeError) as exc:
        raise RenderingCorruptError("render execution manifest is invalid") from exc


def _validate_request(request: LocalRenderRequest) -> None:
    if request.schema_version != LOCAL_RENDER_SCHEMA_VERSION:
        raise RenderingCorruptError("local render request schema is unsupported")
    if render_request_fingerprint(request) != request.request_fingerprint:
        raise RenderingCorruptError("local render request fingerprint differs")


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
