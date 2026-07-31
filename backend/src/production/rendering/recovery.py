"""Pure durable manifest projection for validation and recovery."""

from datetime import datetime

from backend.src.production.rendering.fingerprints import canonical_sha256
from backend.src.production.rendering.models import (
    FFmpegRenderResult,
    LocalRenderRequest,
    LocalRenderResult,
    RendererCapabilities,
    RenderExecutionManifest,
    RenderManifestStatus,
)


def capabilities_fingerprint(capabilities: RendererCapabilities) -> str:
    return canonical_sha256(capabilities.model_dump(mode="json"))


def prepare_manifest(
    *,
    request: LocalRenderRequest,
    attempt_number: int,
    capabilities: RendererCapabilities,
    now: datetime,
) -> RenderExecutionManifest:
    return RenderExecutionManifest(
        schema_version=request.schema_version,
        job_id=request.job_id,
        attempt_number=attempt_number,
        renderer_kind=request.renderer_kind,
        renderer_version=capabilities.renderer_version,
        source_plan_artifact_id=request.source_plan_artifact_id,
        source_plan_relative_path=request.source_plan_relative_path,
        source_plan_sha256=request.source_plan_sha256,
        source_plan_fingerprint=request.source_plan_fingerprint,
        timeline_checksum=request.timeline_checksum,
        request_fingerprint=request.request_fingerprint,
        requested_output=request.requested_output,
        capabilities_fingerprint=capabilities_fingerprint(capabilities),
        status=RenderManifestStatus.PREPARED,
        output_relative_path=request.requested_output.relative_path,
        created_at=now,
        updated_at=now,
        metadata={
            "preparation_only": request.dry_run,
            "real_renderer_executed": False,
        },
    )


def validating_manifest(
    manifest: RenderExecutionManifest,
    *,
    now: datetime,
) -> RenderExecutionManifest:
    return manifest.model_copy(
        update={
            "status": RenderManifestStatus.VALIDATING,
            "updated_at": now,
        }
    )


def validated_manifest(
    manifest: RenderExecutionManifest,
    *,
    result: LocalRenderResult,
    now: datetime,
    output_artifact_id: object | None = None,
) -> RenderExecutionManifest:
    updates: dict[str, object] = {
        "status": RenderManifestStatus.VALIDATED,
        "updated_at": now,
    }
    if isinstance(result, FFmpegRenderResult):
        from uuid import UUID

        if not isinstance(output_artifact_id, UUID):
            raise ValueError("validated FFmpeg result requires an output artifact ID")
        updates.update(
            {
                "ffmpeg_result": result,
                "media_produced": True,
                "output_artifact_id": output_artifact_id,
                "output_sha256": result.output_sha256,
                "output_size_bytes": result.output_size_bytes,
                "metadata": {
                    "preparation_only": False,
                    "real_renderer_executed": True,
                    "validated_by_ffprobe": True,
                },
            }
        )
    else:
        updates["dry_run_result"] = result
    return manifest.model_copy(update=updates)


def ready_to_render_manifest(
    manifest: RenderExecutionManifest,
    *,
    now: datetime,
) -> RenderExecutionManifest:
    return manifest.model_copy(
        update={"status": RenderManifestStatus.READY_TO_RENDER, "updated_at": now}
    )


def rendering_manifest(
    manifest: RenderExecutionManifest,
    *,
    now: datetime,
) -> RenderExecutionManifest:
    return manifest.model_copy(update={"status": RenderManifestStatus.RENDERING, "updated_at": now})


def failed_manifest(
    manifest: RenderExecutionManifest,
    *,
    now: datetime,
    code: str,
) -> RenderExecutionManifest:
    return manifest.model_copy(
        update={
            "status": RenderManifestStatus.FAILED,
            "updated_at": now,
            "metadata": {
                **manifest.metadata,
                "failure_code": code,
                "media_produced": False,
            },
        }
    )


def cancelled_manifest(
    manifest: RenderExecutionManifest,
    *,
    now: datetime,
) -> RenderExecutionManifest:
    return manifest.model_copy(
        update={
            "status": RenderManifestStatus.CANCELLED,
            "updated_at": now,
            "metadata": {
                **manifest.metadata,
                "failure_code": "cancelled",
                "media_produced": False,
            },
        }
    )


def validate_manifest_identity(
    manifest: RenderExecutionManifest,
    *,
    request: LocalRenderRequest,
    capabilities: RendererCapabilities,
) -> None:
    expected = (
        request.job_id,
        request.renderer_kind,
        capabilities.renderer_version,
        request.source_plan_artifact_id,
        request.source_plan_relative_path,
        request.source_plan_sha256,
        request.source_plan_fingerprint,
        request.timeline_checksum,
        request.request_fingerprint,
        request.requested_output,
        capabilities_fingerprint(capabilities),
        request.requested_output.relative_path,
    )
    actual = (
        manifest.job_id,
        manifest.renderer_kind,
        manifest.renderer_version,
        manifest.source_plan_artifact_id,
        manifest.source_plan_relative_path,
        manifest.source_plan_sha256,
        manifest.source_plan_fingerprint,
        manifest.timeline_checksum,
        manifest.request_fingerprint,
        manifest.requested_output,
        manifest.capabilities_fingerprint,
        manifest.output_relative_path,
    )
    if actual != expected:
        from backend.src.production.rendering.exceptions import (
            RenderingStaleSourceError,
        )

        raise RenderingStaleSourceError("render manifest identity differs from request")
