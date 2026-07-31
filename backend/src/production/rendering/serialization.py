"""Strict canonical JSON serialization with identity verification."""

import json
from typing import Any

from backend.src.production.rendering.exceptions import RenderingCorruptError
from backend.src.production.rendering.execution_plan import execution_plan_fingerprint
from backend.src.production.rendering.fingerprints import canonical_json_bytes
from backend.src.production.rendering.models import (
    FFMPEG_EXECUTION_PLAN_SCHEMA_VERSION,
    SUPPORTED_LOCAL_RENDER_VERSIONS,
    FFmpegExecutionPlan,
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
    if manifest.schema_version not in SUPPORTED_LOCAL_RENDER_VERSIONS:
        raise RenderingCorruptError("render execution manifest schema is unsupported")
    return canonical_json_bytes(manifest.model_dump(mode="json"))


def deserialize_render_execution_manifest(
    content: bytes,
) -> RenderExecutionManifest:
    try:
        payload = _deserialize(content)
        if not isinstance(payload, dict) or payload.get("schema_version") not in (
            SUPPORTED_LOCAL_RENDER_VERSIONS
        ):
            raise RenderingCorruptError("render execution manifest schema is unsupported")
        manifest = RenderExecutionManifest.model_validate(payload)
        return manifest
    except RenderingCorruptError:
        raise
    except (TypeError, ValueError, UnicodeError) as exc:
        raise RenderingCorruptError("render execution manifest is invalid") from exc


def _validate_request(request: LocalRenderRequest) -> None:
    if request.schema_version not in SUPPORTED_LOCAL_RENDER_VERSIONS:
        raise RenderingCorruptError("local render request schema is unsupported")
    if render_request_fingerprint(request) != request.request_fingerprint:
        raise RenderingCorruptError("local render request fingerprint differs")


def serialize_ffmpeg_execution_plan(plan: FFmpegExecutionPlan) -> bytes:
    _validate_execution_plan(plan)
    return canonical_json_bytes(plan.model_dump(mode="json"))


def deserialize_ffmpeg_execution_plan(content: bytes) -> FFmpegExecutionPlan:
    try:
        plan = FFmpegExecutionPlan.model_validate(_deserialize(content))
        _validate_execution_plan(plan)
        return plan
    except RenderingCorruptError:
        raise
    except (TypeError, ValueError, UnicodeError) as exc:
        raise RenderingCorruptError("FFmpeg execution plan is invalid") from exc


def _validate_execution_plan(plan: FFmpegExecutionPlan) -> None:
    if plan.schema_version != FFMPEG_EXECUTION_PLAN_SCHEMA_VERSION:
        raise RenderingCorruptError("FFmpeg execution-plan schema is unsupported")
    if execution_plan_fingerprint(plan) != plan.argument_fingerprint:
        raise RenderingCorruptError("FFmpeg execution-plan fingerprint differs")


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
