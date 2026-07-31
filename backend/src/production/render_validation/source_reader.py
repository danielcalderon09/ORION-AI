"""Verify the complete durable provenance chain for final render acceptance."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from uuid import UUID

from backend.src.production.binary_assets.exceptions import BinaryAssetError
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.enums import ArtifactStatus, ArtifactType
from backend.src.production.media_composition.domain.models import (
    CompositionManifestStatus,
    MediaCompositionManifest,
    MediaCompositionPlan,
)
from backend.src.production.media_composition.exceptions import MediaCompositionError
from backend.src.production.media_composition.serialization import (
    deserialize_media_composition_manifest,
    deserialize_media_composition_plan,
)
from backend.src.production.render_validation.exceptions import FinalRenderSourceError
from backend.src.production.render_validation.ports import (
    FinalValidationArtifactInventory,
    FinalValidationStageContext,
    VerifiedFinalRenderSource,
)
from backend.src.production.rendering.exceptions import RenderingError
from backend.src.production.rendering.models import (
    LOCAL_RENDER_SCHEMA_VERSION,
    FFmpegExecutionPlan,
    LocalRenderRequest,
    RendererKind,
    RenderExecutionManifest,
    RenderManifestStatus,
)
from backend.src.production.rendering.serialization import (
    deserialize_ffmpeg_execution_plan,
    deserialize_local_render_request,
    deserialize_render_execution_manifest,
)


class VerifiedFinalRenderSourceReader:
    def __init__(
        self,
        *,
        workspace_root: Path,
        inventory: FinalValidationArtifactInventory,
        max_json_bytes: int,
    ) -> None:
        self._confinement = WorkspaceConfinement(workspace_root)
        self._inventory = inventory
        self._maximum = max_json_bytes

    async def read(
        self,
        *,
        context: FinalValidationStageContext,
        input_artifact_ids: tuple[UUID, ...],
    ) -> VerifiedFinalRenderSource:
        artifacts = await self._inventory.list_for_job(context.job_id)
        return await asyncio.to_thread(
            self._read_sync,
            context,
            input_artifact_ids,
            artifacts,
        )

    def _read_sync(
        self,
        context: FinalValidationStageContext,
        input_artifact_ids: tuple[UUID, ...],
        artifacts: tuple[Artifact, ...],
    ) -> VerifiedFinalRenderSource:
        inputs = {
            item.artifact_id: item for item in artifacts if item.artifact_id in input_artifact_ids
        }
        if len(inputs) != len(set(input_artifact_ids)):
            raise FinalRenderSourceError(
                "render_inputs_missing",
                "registered render-stage inputs are missing",
            )
        render_artifact = _one_type(inputs.values(), ArtifactType.LONG_FORM_RENDER)
        request_artifact = _one_type(inputs.values(), ArtifactType.LOCAL_RENDER_REQUEST)
        execution_plan_artifact = _one_type(
            inputs.values(),
            ArtifactType.FFMPEG_EXECUTION_PLAN,
        )
        render_manifest_artifact = _one_type(
            inputs.values(),
            ArtifactType.RENDER_EXECUTION_MANIFEST,
        )
        for artifact in (
            render_artifact,
            request_artifact,
            execution_plan_artifact,
            render_manifest_artifact,
        ):
            self._validate_registered(artifact, context.job_id)
        try:
            request = deserialize_local_render_request(self._read_json(request_artifact))
            execution_plan = deserialize_ffmpeg_execution_plan(
                self._read_json(execution_plan_artifact)
            )
            render_manifest = deserialize_render_execution_manifest(
                self._read_json(render_manifest_artifact)
            )
        except FinalRenderSourceError:
            raise
        except RenderingError as exc:
            raise FinalRenderSourceError(
                "render_json_corrupt",
                "render request, execution plan, or manifest is invalid",
            ) from exc
        if request.schema_version != LOCAL_RENDER_SCHEMA_VERSION:
            raise FinalRenderSourceError(
                "render_version_unsupported",
                "final validation requires an FFmpeg render request",
            )
        composition_plan_artifact = _one_id(artifacts, request.source_plan_artifact_id)
        self._validate_registered(composition_plan_artifact, context.job_id)
        try:
            composition_plan = deserialize_media_composition_plan(
                self._read_json(composition_plan_artifact)
            )
        except FinalRenderSourceError:
            raise
        except MediaCompositionError as exc:
            raise FinalRenderSourceError(
                "composition_plan_corrupt",
                "media composition plan is invalid",
            ) from exc
        composition_manifest_artifact = _matching_composition_manifest(
            artifacts,
            plan_fingerprint=request.source_plan_fingerprint,
        )
        self._validate_registered(composition_manifest_artifact, context.job_id)
        try:
            composition_manifest = deserialize_media_composition_manifest(
                self._read_json(composition_manifest_artifact)
            )
        except FinalRenderSourceError:
            raise
        except MediaCompositionError as exc:
            raise FinalRenderSourceError(
                "composition_manifest_corrupt",
                "media composition manifest is invalid",
            ) from exc
        render_path = self._media_path(render_artifact)
        actual_size, actual_sha = _hash_file(render_path)
        self._validate_identities(
            request=request,
            execution_plan=execution_plan,
            render_manifest=render_manifest,
            composition_plan=composition_plan,
            composition_manifest=composition_manifest,
            render_artifact=render_artifact,
            composition_plan_artifact=composition_plan_artifact,
            actual_size=actual_size,
            actual_sha=actual_sha,
        )
        return VerifiedFinalRenderSource(
            render_artifact=render_artifact,
            request_artifact=request_artifact,
            execution_plan_artifact=execution_plan_artifact,
            render_manifest_artifact=render_manifest_artifact,
            composition_plan_artifact=composition_plan_artifact,
            composition_manifest_artifact=composition_manifest_artifact,
            request=request,
            execution_plan=execution_plan,
            render_manifest=render_manifest,
            composition_plan=composition_plan,
            composition_manifest=composition_manifest,
            render_path=render_path,
        )

    @staticmethod
    def _validate_identities(
        *,
        request: LocalRenderRequest,
        execution_plan: FFmpegExecutionPlan,
        render_manifest: RenderExecutionManifest,
        composition_plan: MediaCompositionPlan,
        composition_manifest: MediaCompositionManifest,
        render_artifact: Artifact,
        composition_plan_artifact: Artifact,
        actual_size: int,
        actual_sha: str,
    ) -> None:
        if (
            request.job_id != render_artifact.job_id
            or render_manifest.job_id != render_artifact.job_id
            or composition_plan.job_id != render_artifact.job_id
            or composition_manifest.job_id != render_artifact.job_id
            or request.renderer_kind is not RendererKind.FFMPEG
            or request.dry_run
            or render_manifest.renderer_kind is not RendererKind.FFMPEG
            or render_manifest.status is not RenderManifestStatus.VALIDATED
            or not render_manifest.media_produced
            or render_manifest.ffmpeg_result is None
            or render_manifest.output_artifact_id != render_artifact.artifact_id
            or render_manifest.output_relative_path != render_artifact.relative_path
            or render_manifest.output_sha256 != render_artifact.sha256
            or render_manifest.output_size_bytes != render_artifact.size_bytes
            or actual_size != render_artifact.size_bytes
            or actual_sha != render_artifact.sha256
            or actual_size > execution_plan.execution_policy.max_output_bytes
            or execution_plan.request_fingerprint != request.request_fingerprint
            or execution_plan.output_relative_path != render_artifact.relative_path
            or execution_plan.expected_output != request.requested_output
            or request.requested_output.relative_path != render_artifact.relative_path
            or render_artifact.mime_type != request.requested_output.expected_mime_type
            or request.source_plan_artifact_id != composition_plan_artifact.artifact_id
            or request.source_plan_relative_path != composition_plan_artifact.relative_path
            or request.source_plan_sha256 != composition_plan_artifact.sha256
            or request.source_plan_fingerprint != composition_plan.plan_fingerprint
            or request.timeline_checksum != composition_plan.timeline_checksum
            or composition_manifest.status is not CompositionManifestStatus.COMPLETE
            or composition_manifest.plan_fingerprint != composition_plan.plan_fingerprint
            or composition_manifest.timeline_checksum != composition_plan.timeline_checksum
            or composition_manifest.plan_sha256 != composition_plan_artifact.sha256
            or composition_manifest.plan_relative_path != composition_plan_artifact.relative_path
            or render_manifest.source_plan_artifact_id != composition_plan_artifact.artifact_id
            or render_manifest.source_plan_relative_path != composition_plan_artifact.relative_path
            or render_manifest.source_plan_sha256 != composition_plan_artifact.sha256
            or render_manifest.source_plan_fingerprint != composition_plan.plan_fingerprint
            or render_manifest.timeline_checksum != composition_plan.timeline_checksum
            or render_manifest.request_fingerprint != request.request_fingerprint
            or render_manifest.requested_output != request.requested_output
            or (
                request.output_width,
                request.output_height,
                request.frame_rate_numerator,
                request.frame_rate_denominator,
                request.expected_duration_ms,
                request.expected_duration_frames,
            )
            != (
                composition_plan.output.width,
                composition_plan.output.height,
                composition_plan.output.frame_rate_numerator,
                composition_plan.output.frame_rate_denominator,
                composition_plan.output.expected_duration_ms,
                composition_plan.output.expected_duration_frames,
            )
            or render_artifact.metadata.get("request_fingerprint") != request.request_fingerprint
            or render_artifact.metadata.get("source_plan_fingerprint")
            != composition_plan.plan_fingerprint
            or render_artifact.metadata.get("timeline_checksum")
            != composition_plan.timeline_checksum
            or render_artifact.metadata.get("validated_by_ffprobe") is not True
        ):
            raise FinalRenderSourceError(
                "render_provenance_mismatch",
                "render provenance chain is incomplete or inconsistent",
            )

    @staticmethod
    def _validate_registered(artifact: Artifact, job_id: UUID) -> None:
        if (
            artifact.job_id != job_id
            or artifact.status is not ArtifactStatus.READY
            or artifact.size_bytes is None
            or artifact.size_bytes <= 0
            or artifact.sha256 is None
        ):
            raise FinalRenderSourceError(
                "render_artifact_invalid",
                "registered render provenance artifact is not READY and complete",
            )

    def _read_json(self, artifact: Artifact) -> bytes:
        if artifact.size_bytes is None or artifact.sha256 is None:
            raise FinalRenderSourceError(
                "render_json_incomplete", "render JSON metadata is incomplete"
            )
        if artifact.size_bytes > self._maximum:
            raise FinalRenderSourceError("render_json_oversized", "render JSON exceeds its limit")
        try:
            target = self._confinement.resolve(artifact.relative_path, require_exists=True)
            self._confinement.reject_unsafe_file(target)
            if target.stat().st_size != artifact.size_bytes:
                raise FinalRenderSourceError("render_json_changed", "render JSON size changed")
            with target.open("rb") as stream:
                content = stream.read(self._maximum + 1)
        except FinalRenderSourceError:
            raise
        except (BinaryAssetError, OSError) as exc:
            raise FinalRenderSourceError(
                "render_json_missing",
                "render JSON is missing or unsafe",
            ) from exc
        if len(content) != artifact.size_bytes or hashlib.sha256(content).hexdigest() != (
            artifact.sha256
        ):
            raise FinalRenderSourceError("render_json_corrupt", "render JSON checksum differs")
        return content

    def _media_path(self, artifact: Artifact) -> Path:
        try:
            target = self._confinement.resolve(artifact.relative_path, require_exists=True)
            self._confinement.reject_unsafe_file(target)
            return target
        except (BinaryAssetError, OSError) as exc:
            raise FinalRenderSourceError(
                "render_file_missing",
                "LONG_FORM_RENDER is missing or unsafe",
            ) from exc


def _one_type(artifacts: Iterable[Artifact], artifact_type: ArtifactType) -> Artifact:
    candidates = tuple(item for item in artifacts if item.artifact_type is artifact_type)
    if len(candidates) != 1:
        raise FinalRenderSourceError(
            f"{artifact_type.value}_missing",
            f"exactly one input {artifact_type.value} artifact is required",
        )
    return candidates[0]


def _one_id(artifacts: tuple[Artifact, ...], artifact_id: UUID) -> Artifact:
    candidates = tuple(item for item in artifacts if item.artifact_id == artifact_id)
    if len(candidates) != 1:
        raise FinalRenderSourceError(
            "composition_plan_missing",
            "expected media composition plan artifact is missing",
        )
    return candidates[0]


def _matching_composition_manifest(
    artifacts: tuple[Artifact, ...],
    *,
    plan_fingerprint: str,
) -> Artifact:
    candidates = tuple(
        item
        for item in artifacts
        if item.artifact_type is ArtifactType.MEDIA_COMPOSITION_MANIFEST
        and item.status is ArtifactStatus.READY
        and item.metadata.get("plan_fingerprint") == plan_fingerprint
    )
    if not candidates:
        raise FinalRenderSourceError(
            "composition_manifest_missing",
            "expected media composition manifest artifact is missing",
        )
    latest = max(_attempt(item.relative_path) for item in candidates)
    selected = tuple(item for item in candidates if _attempt(item.relative_path) == latest)
    if latest < 1 or len(selected) != 1:
        raise FinalRenderSourceError(
            "composition_manifest_conflict",
            "composition manifest selection is ambiguous",
        )
    return selected[0]


def _attempt(relative_path: str) -> int:
    for part in PurePosixPath(relative_path).parts:
        if part.startswith("attempt-") and part[8:].isdigit():
            return int(part[8:])
    return -1


def _hash_file(path: Path) -> tuple[int, str]:
    try:
        status = path.stat()
        if status.st_size <= 0 or status.st_nlink != 1:
            raise FinalRenderSourceError(
                "render_file_invalid",
                "LONG_FORM_RENDER is empty or linked",
            )
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1_048_576):
                digest.update(chunk)
    except FinalRenderSourceError:
        raise
    except OSError as exc:
        raise FinalRenderSourceError(
            "render_file_missing",
            "LONG_FORM_RENDER cannot be read",
        ) from exc
    return status.st_size, digest.hexdigest()
