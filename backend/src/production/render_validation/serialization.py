"""Canonical final-validation JSON with duplicate-key rejection."""

import json
from typing import Any

from backend.src.production.render_validation.exceptions import FinalRenderCorruptError
from backend.src.production.render_validation.fingerprints import (
    final_validation_fingerprint,
)
from backend.src.production.render_validation.models import (
    FINAL_RENDER_VALIDATION_SCHEMA_VERSION,
    FinalRenderValidationManifest,
    FinalValidationStatus,
)
from backend.src.production.rendering.fingerprints import canonical_json_bytes


def serialize_final_render_validation(manifest: FinalRenderValidationManifest) -> bytes:
    _validate(manifest)
    return canonical_json_bytes(manifest.model_dump(mode="json"))


def deserialize_final_render_validation(content: bytes) -> FinalRenderValidationManifest:
    try:
        payload = json.loads(
            content.decode("utf-8", errors="strict"),
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
        if not isinstance(payload, dict) or payload.get("schema_version") != (
            FINAL_RENDER_VALIDATION_SCHEMA_VERSION
        ):
            raise FinalRenderCorruptError(
                "validation_schema_unsupported",
                "final-render validation schema is unsupported",
            )
        manifest = FinalRenderValidationManifest.model_validate(payload)
        _validate(manifest)
        return manifest
    except FinalRenderCorruptError:
        raise
    except (TypeError, UnicodeError, ValueError) as exc:
        raise FinalRenderCorruptError(
            "validation_manifest_corrupt",
            "final-render validation manifest is invalid",
        ) from exc


def _validate(manifest: FinalRenderValidationManifest) -> None:
    if manifest.status not in {
        FinalValidationStatus.VALIDATED,
        FinalValidationStatus.FAILED,
    }:
        if manifest.validation_fingerprint is not None:
            raise FinalRenderCorruptError(
                "validation_fingerprint_premature",
                "non-terminal final validation has a fingerprint",
            )
        return
    expected = final_validation_fingerprint(
        job_id=manifest.job_id,
        render_artifact_id=manifest.render_artifact_id,
        render_relative_path=manifest.render_relative_path,
        render_checksum=manifest.render_checksum,
        render_size_bytes=manifest.render_size_bytes,
        result=manifest.validation_result,
        warnings=manifest.warnings,
        error_codes=manifest.error_codes,
        fingerprints=manifest.fingerprints,
        ffprobe_summary=manifest.ffprobe_summary,
    )
    if manifest.validation_fingerprint != expected:
        raise FinalRenderCorruptError(
            "validation_fingerprint_mismatch",
            "final-render validation fingerprint differs",
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
