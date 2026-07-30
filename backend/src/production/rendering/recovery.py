"""Pure durable manifest projection for validation and recovery."""

from datetime import datetime

from backend.src.production.rendering.fingerprints import canonical_sha256
from backend.src.production.rendering.models import (
    DryRunRenderResult,
    LocalRenderRequest,
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
        job_id=request.job_id,
        attempt_number=attempt_number,
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
            "preparation_only": True,
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
    result: DryRunRenderResult,
    now: datetime,
) -> RenderExecutionManifest:
    return manifest.model_copy(
        update={
            "status": RenderManifestStatus.VALIDATED,
            "dry_run_result": result,
            "updated_at": now,
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
