"""Pure projections for durable final-render validation recovery."""

from datetime import datetime
from uuid import UUID

from backend.src.production.render_validation.fingerprints import (
    final_validation_fingerprint,
)
from backend.src.production.render_validation.models import (
    FinalFFprobeSummary,
    FinalRenderFingerprints,
    FinalRenderValidationManifest,
    FinalValidationResult,
    FinalValidationStatus,
)
from backend.src.production.render_validation.ports import VerifiedFinalRenderSource
from backend.src.production.rendering.output_probe import ProbedRenderOutput


def prepared_manifest(
    *,
    source: VerifiedFinalRenderSource,
    attempt_number: int,
    now: datetime,
) -> FinalRenderValidationManifest:
    fingerprints = _source_fingerprints(source)
    return FinalRenderValidationManifest(
        job_id=source.render_artifact.job_id,
        attempt_number=attempt_number,
        status=FinalValidationStatus.PREPARED,
        render_artifact_id=source.render_artifact.artifact_id,
        render_relative_path=source.render_artifact.relative_path,
        render_checksum=source.render_artifact.sha256,
        render_size_bytes=source.render_artifact.size_bytes,
        render_manifest_artifact_id=source.render_manifest_artifact.artifact_id,
        media_composition_plan_artifact_id=source.composition_plan_artifact.artifact_id,
        execution_plan_artifact_id=source.execution_plan_artifact.artifact_id,
        validation_result=FinalValidationResult.PENDING,
        fingerprints=fingerprints,
        plan_fingerprint=fingerprints.plan_fingerprint,
        execution_plan_fingerprint=fingerprints.execution_plan_fingerprint,
        created_at=now,
        updated_at=now,
        metadata={"ffmpeg_executed": False, "render_modified": False},
    )


def validating_manifest(
    manifest: FinalRenderValidationManifest,
    *,
    now: datetime,
) -> FinalRenderValidationManifest:
    return manifest.model_copy(
        update={"status": FinalValidationStatus.VALIDATING, "updated_at": now}
    )


def validated_manifest(
    manifest: FinalRenderValidationManifest,
    *,
    probe: ProbedRenderOutput,
    now: datetime,
) -> FinalRenderValidationManifest:
    summary = _probe_summary(probe)
    fingerprints = manifest.fingerprints.model_copy(
        update={"probe_fingerprint": probe.probe_fingerprint}
    )
    fingerprint = final_validation_fingerprint(
        job_id=manifest.job_id,
        render_artifact_id=manifest.render_artifact_id,
        render_relative_path=manifest.render_relative_path,
        render_checksum=manifest.render_checksum,
        render_size_bytes=manifest.render_size_bytes,
        result=FinalValidationResult.PASSED,
        warnings=(),
        error_codes=(),
        fingerprints=fingerprints,
        ffprobe_summary=summary,
    )
    return manifest.model_copy(
        update={
            "status": FinalValidationStatus.VALIDATED,
            "validation_timestamp": now,
            "ffprobe_summary": summary,
            "validation_result": FinalValidationResult.PASSED,
            "fingerprints": fingerprints,
            "validation_fingerprint": fingerprint,
            "updated_at": now,
        }
    )


def failed_manifest(
    manifest: FinalRenderValidationManifest,
    *,
    code: str,
    now: datetime,
) -> FinalRenderValidationManifest:
    codes = tuple(sorted({*manifest.error_codes, code}))
    fingerprint = final_validation_fingerprint(
        job_id=manifest.job_id,
        render_artifact_id=manifest.render_artifact_id,
        render_relative_path=manifest.render_relative_path,
        render_checksum=manifest.render_checksum,
        render_size_bytes=manifest.render_size_bytes,
        result=FinalValidationResult.FAILED,
        warnings=manifest.warnings,
        error_codes=codes,
        fingerprints=manifest.fingerprints,
        ffprobe_summary=manifest.ffprobe_summary,
    )
    return manifest.model_copy(
        update={
            "status": FinalValidationStatus.FAILED,
            "validation_timestamp": now,
            "validation_result": FinalValidationResult.FAILED,
            "error_codes": codes,
            "validation_fingerprint": fingerprint,
            "updated_at": now,
        }
    )


def source_failure_manifest(
    *,
    job_id: UUID,
    attempt_number: int,
    code: str,
    now: datetime,
) -> FinalRenderValidationManifest:
    fingerprints = FinalRenderFingerprints()
    codes = (code,)
    fingerprint = final_validation_fingerprint(
        job_id=job_id,
        render_artifact_id=None,
        render_relative_path=None,
        render_checksum=None,
        render_size_bytes=None,
        result=FinalValidationResult.FAILED,
        warnings=(),
        error_codes=codes,
        fingerprints=fingerprints,
        ffprobe_summary=None,
    )
    return FinalRenderValidationManifest(
        job_id=job_id,
        attempt_number=attempt_number,
        status=FinalValidationStatus.FAILED,
        validation_timestamp=now,
        validation_result=FinalValidationResult.FAILED,
        error_codes=codes,
        fingerprints=fingerprints,
        validation_fingerprint=fingerprint,
        created_at=now,
        updated_at=now,
        metadata={"ffmpeg_executed": False, "render_modified": False},
    )


def _source_fingerprints(source: VerifiedFinalRenderSource) -> FinalRenderFingerprints:
    return FinalRenderFingerprints(
        request_fingerprint=source.request.request_fingerprint,
        plan_fingerprint=source.composition_plan.plan_fingerprint,
        timeline_checksum=source.composition_plan.timeline_checksum,
        execution_plan_fingerprint=source.execution_plan.argument_fingerprint,
        render_checksum=source.render_artifact.sha256,
    )


def _probe_summary(probe: ProbedRenderOutput) -> FinalFFprobeSummary:
    return FinalFFprobeSummary(
        duration_ms=probe.duration_ms,
        duration_frames=probe.duration_frames,
        width=probe.width,
        height=probe.height,
        frame_rate_numerator=probe.frame_rate_numerator,
        frame_rate_denominator=probe.frame_rate_denominator,
        video_codec=probe.video_codec,
        audio_codec=probe.audio_codec,
        pixel_format=probe.pixel_format,
        video_stream_count=probe.video_stream_count,
        audio_stream_count=probe.audio_stream_count,
        subtitle_stream_count=probe.subtitle_stream_count,
        format_names=probe.format_names,
        probe_fingerprint=probe.probe_fingerprint,
    )
