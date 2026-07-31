"""Canonical final-validation identities."""

from backend.src.production.render_validation.models import (
    FinalFFprobeSummary,
    FinalRenderFingerprints,
    FinalValidationResult,
)
from backend.src.production.rendering.fingerprints import canonical_sha256


def final_validation_fingerprint(
    *,
    job_id: object,
    render_artifact_id: object | None,
    render_relative_path: str | None,
    render_checksum: str | None,
    render_size_bytes: int | None,
    result: FinalValidationResult,
    warnings: tuple[str, ...],
    error_codes: tuple[str, ...],
    fingerprints: FinalRenderFingerprints,
    ffprobe_summary: FinalFFprobeSummary | None,
) -> str:
    return canonical_sha256(
        {
            "error_codes": error_codes,
            "ffprobe_summary": (
                ffprobe_summary.model_dump(mode="json") if ffprobe_summary is not None else None
            ),
            "fingerprints": fingerprints.model_dump(mode="json"),
            "job_id": str(job_id),
            "render_artifact_id": (
                str(render_artifact_id) if render_artifact_id is not None else None
            ),
            "render_checksum": render_checksum,
            "render_relative_path": render_relative_path,
            "render_size_bytes": render_size_bytes,
            "result": result.value,
            "schema_version": "1.0.0",
            "warnings": warnings,
        }
    )
