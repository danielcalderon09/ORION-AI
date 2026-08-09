"""Sequential durable provider-neutral video clip generation handler."""

import asyncio
import hashlib
import logging
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome, StageResult
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.enums import (
    ArtifactStatus,
    ArtifactType,
    ProductionStage,
)
from backend.src.production.runtime.context import StageContext
from backend.src.production.runtime.runtime_models import StageExecutionOutput
from backend.src.production.video_clip_generation.configuration import (
    VideoClipGenerationConfiguration,
)
from backend.src.production.video_clip_generation.exceptions import (
    ImageAcquisitionManifestReadError,
    OpenRouterVideoError,
    OpenRouterVideoRateLimitError,
    OpenRouterVideoServerError,
    OpenRouterVideoTransportError,
    OpenRouterVideoUncertainSubmissionError,
    VideoClipConflictError,
    VideoClipGenerationError,
    VideoClipIntegrityError,
    VideoClipManifestError,
    VideoClipNotFoundError,
    VideoClipProviderDependencyException,
    VideoClipProviderError,
    VideoClipProviderResponseException,
    VideoClipProviderTimeoutException,
)
from backend.src.production.video_clip_generation.manifest_writer import (
    video_clip_manifest_relative_path,
)
from backend.src.production.video_clip_generation.models import (
    ProductionVideoClipAsset,
    ProductionVideoClipEntry,
    ProductionVideoClipManifest,
    VideoClipEntryStatus,
    VideoClipManifestStatus,
    VideoClipMetadata,
    VideoClipRemoteStatus,
    VideoClipWriteRequest,
    replace_manifest_entry,
    summarize_entries,
)
from backend.src.production.video_clip_generation.ports import (
    ImageAcquisitionManifestReader,
    ReadImageAcquisitionManifest,
    VerifiedSourceImage,
    VideoClipBinaryStore,
    VideoClipGenerationProvider,
    VideoClipManifestWriter,
    VideoClipProviderRequest,
    VideoClipProviderResponse,
)
from backend.src.production.video_clip_generation.prompt_builder import (
    VideoClipAnimationRecipeBuilder,
)
from backend.src.production.video_clip_generation.serialization import (
    serialize_video_clip_manifest,
)
from backend.src.production.visual_asset_planning.models import VisualAssetRole

logger = logging.getLogger(__name__)


class VideoClipGenerationHandler:
    supported_stages = frozenset({ProductionStage.GENERATING_VIDEO_CLIPS})

    def __init__(
        self,
        *,
        manifest_reader: ImageAcquisitionManifestReader,
        provider: VideoClipGenerationProvider,
        binary_store: VideoClipBinaryStore,
        manifest_writer: VideoClipManifestWriter,
        configuration: VideoClipGenerationConfiguration,
        clock: Callable[[], datetime],
        recipe_builder: VideoClipAnimationRecipeBuilder | None = None,
    ) -> None:
        self._reader = manifest_reader
        self._provider = provider
        self._store = binary_store
        self._writer = manifest_writer
        self._configuration = configuration
        self._clock = clock
        self._recipe_builder = recipe_builder or VideoClipAnimationRecipeBuilder()

    async def execute(self, command: StageCommand, context: StageContext) -> StageExecutionOutput:
        if command.stage is not ProductionStage.GENERATING_VIDEO_CLIPS:
            raise ValueError("handler supports only generating_video_clips")
        if context.command_id != command.command_id:
            raise ValueError("StageContext does not belong to StageCommand")
        started_at = self._aware_now()
        try:
            source = await self._reader.read_for_video_clip_generation(context=context)
            existing = await self._writer.read_existing(context=context)
            manifest = existing or self._initial_manifest(
                source=source, attempt_number=command.attempt_number
            )
            if existing is None:
                await self._writer.create(context=context, manifest=manifest)
            else:
                self._validate_manifest_source(manifest, source)

            stored_assets: dict[str, ProductionVideoClipAsset] = {}
            initially_pending = tuple(
                image
                for image in source.source_images
                if _entry_for(manifest, image.visual_asset_id).status
                is VideoClipEntryStatus.PENDING
            )
            for image in initially_pending:
                entry = _entry_for(manifest, image.visual_asset_id)
                recovered = await self._recover_optional(entry, image, source)
                if recovered is None:
                    continue
                recovered_requested_duration = recovered.metadata.attributes.get(
                    "requested_duration_seconds"
                )
                recovering = entry.model_copy(
                    update={
                        "status": VideoClipEntryStatus.GENERATING,
                        "requested_duration_seconds": (
                            float(recovered_requested_duration)
                            if isinstance(recovered_requested_duration, (int, float))
                            else recovered.duration_seconds
                        ),
                        "error_code": None,
                    }
                )
                current = replace_manifest_entry(manifest, recovering)
                await self._writer.checkpoint(
                    context=context,
                    previous=manifest,
                    current=current,
                )
                manifest = current
                stored = self._stored_entry(
                    entry=recovering,
                    asset=recovered,
                    response=None,
                    recovered=True,
                )
                current = replace_manifest_entry(manifest, stored)
                await self._writer.checkpoint(
                    context=context,
                    previous=manifest,
                    current=current,
                )
                manifest = current
                stored_assets[image.visual_asset_id] = recovered

            pending_images = tuple(
                image
                for image in source.source_images
                if _entry_for(manifest, image.visual_asset_id).status
                is VideoClipEntryStatus.PENDING
            )
            prepared_requests = {
                image.visual_asset_id: self._provider_request(command, context, image)
                for image in pending_images
            }
            preflight = getattr(self._provider, "preflight_job", None)
            if prepared_requests and callable(preflight):
                try:
                    planned = await preflight(tuple(prepared_requests.values()))
                    prepared_requests = {item.visual_asset_id: item for item in planned}
                except OpenRouterVideoError as exc:
                    image = pending_images[0]
                    entry = _entry_for(manifest, image.visual_asset_id)
                    error_code = _pre_submission_error_code(exc) or "video_clip_provider_contract"
                    diagnostic = self._provider_diagnostic_metadata(exc, image=image)
                    await self._checkpoint_error(
                        context=context,
                        manifest=manifest,
                        entry=entry,
                        status=VideoClipEntryStatus.FAILED_PERMANENT,
                        error_code=error_code,
                        diagnostic_metadata=diagnostic,
                    )
                    return self._failure(
                        command,
                        started_at,
                        StageOutcome.FAILED_PERMANENT,
                        error_code,
                        diagnostic_metadata=diagnostic,
                    )

            for image in source.source_images:
                entry = _entry_for(manifest, image.visual_asset_id)
                if entry.status is VideoClipEntryStatus.STORED:
                    asset = await self._recover_required(entry, image, source)
                    recovered_entry = entry.model_copy(
                        update={
                            "metadata": {
                                **entry.metadata,
                                "recovered": True,
                                "simulated": asset.metadata.attributes.get("simulated", False),
                                "deterministic": asset.metadata.deterministic,
                            }
                        }
                    )
                    if recovered_entry != entry:
                        current = replace_manifest_entry(
                            manifest,
                            recovered_entry,
                        )
                        await self._writer.checkpoint(
                            context=context,
                            previous=manifest,
                            current=current,
                        )
                        manifest = current
                    stored_assets[image.visual_asset_id] = asset
                    continue
                if entry.status is VideoClipEntryStatus.GENERATING:
                    recovered = await self._recover_optional(entry, image, source)
                    if recovered is None:
                        uncertain = entry.model_copy(
                            update={
                                "status": VideoClipEntryStatus.UNCERTAIN,
                                "error_code": "generation_interrupted",
                            }
                        )
                        current = replace_manifest_entry(
                            manifest,
                            uncertain,
                            status=VideoClipManifestStatus.UNCERTAIN,
                        )
                        await self._writer.checkpoint(
                            context=context, previous=manifest, current=current
                        )
                        return self._failure(
                            command,
                            started_at,
                            StageOutcome.NEEDS_USER_ACTION,
                            "video_generation_uncertain",
                        )
                    stored = self._stored_entry(
                        entry=entry,
                        asset=recovered,
                        response=None,
                        recovered=True,
                    )
                    current = replace_manifest_entry(manifest, stored)
                    await self._writer.checkpoint(
                        context=context, previous=manifest, current=current
                    )
                    manifest = current
                    stored_assets[image.visual_asset_id] = recovered
                    continue
                if entry.status in {
                    VideoClipEntryStatus.UNCERTAIN,
                    VideoClipEntryStatus.FAILED_PERMANENT,
                    VideoClipEntryStatus.FAILED_TRANSIENT,
                }:
                    return self._failure(
                        command,
                        started_at,
                        StageOutcome.NEEDS_USER_ACTION
                        if entry.status is VideoClipEntryStatus.UNCERTAIN
                        else StageOutcome.FAILED_PERMANENT,
                        entry.error_code or "video_clip_entry_terminal",
                    )

                recovered = await self._recover_optional(entry, image, source)
                if recovered is not None:
                    recovering = entry.model_copy(
                        update={
                            "status": VideoClipEntryStatus.GENERATING,
                            "error_code": None,
                        }
                    )
                    current = replace_manifest_entry(manifest, recovering)
                    await self._writer.checkpoint(
                        context=context, previous=manifest, current=current
                    )
                    manifest = current
                    stored = self._stored_entry(
                        entry=recovering,
                        asset=recovered,
                        response=None,
                        recovered=True,
                    )
                    current = replace_manifest_entry(manifest, stored)
                    await self._writer.checkpoint(
                        context=context, previous=manifest, current=current
                    )
                    manifest = current
                    stored_assets[image.visual_asset_id] = recovered
                    continue

                try:
                    provider_request = prepared_requests.get(
                        image.visual_asset_id
                    ) or self._provider_request(command, context, image)
                except (TypeError, ValueError, ValidationError):
                    diagnostic = self._request_diagnostic_metadata(
                        image=image,
                        phase="request_construction",
                        diagnostic_code="request_model_validation",
                    )
                    await self._checkpoint_error(
                        context=context,
                        manifest=manifest,
                        entry=entry,
                        status=VideoClipEntryStatus.FAILED_PERMANENT,
                        error_code="video_clip_request_invalid",
                        diagnostic_metadata=diagnostic,
                    )
                    return self._failure(
                        command,
                        started_at,
                        StageOutcome.FAILED_PERMANENT,
                        "video_clip_request_invalid",
                        diagnostic_metadata=diagnostic,
                    )
                generating = entry.model_copy(
                    update={
                        "status": VideoClipEntryStatus.GENERATING,
                        "requested_duration_seconds": provider_request.duration_seconds,
                        "error_code": None,
                    }
                )
                current = replace_manifest_entry(manifest, generating)
                await self._writer.checkpoint(
                    context=context, previous=manifest, current=current
                )
                manifest = current
                try:
                    try:
                        response = await self._provider.generate_clip(provider_request)
                    except OpenRouterVideoError as exc:
                        mapped_error_code = _pre_submission_error_code(exc)
                        if mapped_error_code is None:
                            raise
                        diagnostic = self._provider_diagnostic_metadata(exc, image=image)
                        await self._checkpoint_error(
                            context=context,
                            manifest=manifest,
                            entry=generating,
                            status=VideoClipEntryStatus.FAILED_PERMANENT,
                            error_code=mapped_error_code,
                            diagnostic_metadata=diagnostic,
                        )
                        return self._failure(
                            command,
                            started_at,
                            StageOutcome.FAILED_PERMANENT,
                            mapped_error_code,
                            diagnostic_metadata=diagnostic,
                        )
                    payload = response.clips[0]
                    if len(response.clips) != 1 or payload.mime_type != "video/mp4":
                        raise VideoClipProviderResponseException(
                            "provider must return exactly one MP4 clip"
                        )
                    asset = await self._store.write(
                        request=self._write_request(
                            source=source,
                            image=image,
                            provider_request=provider_request,
                            response=response,
                        ),
                        content=payload.content,
                    )
                    verified = await self._store.read(asset=asset)
                    stored = self._stored_entry(
                        entry=generating,
                        asset=verified.asset,
                        response=response,
                        recovered=False,
                    )
                    current = replace_manifest_entry(manifest, stored)
                    await self._writer.checkpoint(
                        context=context, previous=manifest, current=current
                    )
                    manifest = current
                    stored_assets[image.visual_asset_id] = verified.asset
                    logger.info(
                        "video clip stored",
                        extra={
                            "job_id": str(command.job_id),
                            "command_id": str(command.command_id),
                            "attempt": command.attempt_number,
                            "visual_asset_id": image.visual_asset_id,
                            "provider": response.provider,
                            "reported_model": response.reported_model,
                            "duration_seconds": verified.asset.duration_seconds,
                            "frame_rate": verified.asset.frame_rate,
                            "frame_count": verified.asset.frame_count,
                            "video_codec": verified.asset.video_codec,
                            "has_audio": False,
                            "size_bytes": verified.asset.size_bytes,
                            "latency_ms": response.latency_ms,
                            "cost_usd": (
                                str(response.cost_usd) if response.cost_usd is not None else None
                            ),
                            "simulated": response.metadata.get("simulated", False),
                            "recovered": False,
                        },
                    )
                except asyncio.CancelledError:
                    raise
                except OpenRouterVideoUncertainSubmissionError:
                    await self._checkpoint_error(
                        context=context,
                        manifest=manifest,
                        entry=generating,
                        status=VideoClipEntryStatus.UNCERTAIN,
                        error_code="video_remote_uncertain",
                    )
                    return self._failure(
                        command,
                        started_at,
                        StageOutcome.NEEDS_USER_ACTION,
                        "video_remote_uncertain",
                    )
                except (
                    OpenRouterVideoRateLimitError,
                    OpenRouterVideoServerError,
                    OpenRouterVideoTransportError,
                ):
                    await self._checkpoint_error(
                        context=context,
                        manifest=manifest,
                        entry=generating,
                        status=VideoClipEntryStatus.FAILED_TRANSIENT,
                        error_code="video_provider_transient",
                    )
                    return self._failure(
                        command,
                        started_at,
                        StageOutcome.FAILED_TRANSIENT,
                        "video_provider_transient",
                        retry_after_seconds=5,
                    )
                except VideoClipProviderTimeoutException:
                    await self._checkpoint_error(
                        context=context,
                        manifest=manifest,
                        entry=generating,
                        status=VideoClipEntryStatus.FAILED_TRANSIENT,
                        error_code="video_provider_timeout",
                    )
                    return self._failure(
                        command,
                        started_at,
                        StageOutcome.FAILED_TRANSIENT,
                        "video_provider_timeout",
                        retry_after_seconds=1,
                    )
                except VideoClipProviderDependencyException:
                    await self._checkpoint_error(
                        context=context,
                        manifest=manifest,
                        entry=generating,
                        status=VideoClipEntryStatus.FAILED_PERMANENT,
                        error_code="video_dependency_unavailable",
                    )
                    return self._failure(
                        command,
                        started_at,
                        StageOutcome.FAILED_PERMANENT,
                        "video_dependency_unavailable",
                    )
                except (VideoClipProviderError, VideoClipIntegrityError, VideoClipConflictError):
                    await self._checkpoint_error(
                        context=context,
                        manifest=manifest,
                        entry=generating,
                        status=VideoClipEntryStatus.FAILED_PERMANENT,
                        error_code="video_clip_invalid",
                    )
                    return self._failure(
                        command,
                        started_at,
                        StageOutcome.FAILED_PERMANENT,
                        "video_clip_invalid",
                    )

            completed = manifest.model_copy(update={"status": VideoClipManifestStatus.COMPLETED})
            await self._writer.finalize(context=context, previous=manifest, current=completed)
            return self._success(
                command=command,
                context=context,
                source=source,
                manifest=completed,
                stored_assets=stored_assets,
                started_at=started_at,
            )
        except asyncio.CancelledError:
            raise
        except ImageAcquisitionManifestReadError:
            return self._failure(
                command,
                started_at,
                StageOutcome.FAILED_PERMANENT,
                "source_image_manifest_invalid",
            )
        except VideoClipManifestError:
            return self._failure(
                command,
                started_at,
                StageOutcome.FAILED_PERMANENT,
                "video_clip_manifest_invalid",
            )
        except VideoClipGenerationError:
            return self._failure(
                command,
                started_at,
                StageOutcome.FAILED_PERMANENT,
                "video_clip_generation_error",
            )

    def _initial_manifest(
        self, *, source: ReadImageAcquisitionManifest, attempt_number: int
    ) -> ProductionVideoClipManifest:
        entries = tuple(
            ProductionVideoClipEntry(
                visual_asset_id=image.visual_asset_id,
                source_image_artifact_id=image.artifact_id,
                source_image_binary_asset_id=image.binary_asset_id,
                source_image_sha256=image.sha256,
                source_scene_id=image.scene_id,
                source_shot_id=image.shot_id,
                scene_number=image.scene_number,
                shot_number=image.shot_number,
                role=VisualAssetRole(image.role),
                status=VideoClipEntryStatus.PENDING,
                planned_duration_seconds=image.planned_duration_seconds,
                resolved_scene_duration_ms=(
                    round(image.resolved_duration_seconds * 1_000)
                    if image.resolved_duration_seconds is not None
                    else None
                ),
                attempt_number=attempt_number,
            )
            for image in sorted(
                source.source_images,
                key=lambda item: (item.scene_number, item.shot_number, item.visual_asset_id),
            )
        )
        return ProductionVideoClipManifest(
            source_image_manifest_schema_version=source.schema_version,
            source_image_manifest_artifact_id=source.artifact_id,
            source_image_manifest_sha256=source.sha256,
            provider=self._configuration.provider,
            requested_model=self._configuration.model,
            configuration_fingerprint=self._configuration.fingerprint(),
            entries=entries,
            summary=summarize_entries(entries),
            status=VideoClipManifestStatus.IN_PROGRESS,
            metadata={
                "sequential": True,
                "checkpointed": True,
                "simulated": self._configuration.provider == "simulated",
                "generation_mode": "still_image_to_video",
                "requested_target_duration_ms": (
                    source.duration_resolution.requested_target_duration_ms
                    if source.duration_resolution is not None
                    else None
                ),
                "resolved_duration_ms": (
                    source.duration_resolution.resolved_duration_ms
                    if source.duration_resolution is not None
                    else None
                ),
            },
        )

    def _validate_manifest_source(
        self,
        manifest: ProductionVideoClipManifest,
        source: ReadImageAcquisitionManifest,
    ) -> None:
        ordered_images = tuple(
            sorted(
                source.source_images,
                key=lambda item: (item.scene_number, item.shot_number, item.visual_asset_id),
            )
        )
        manifest_resolved = tuple(
            entry.resolved_scene_duration_ms for entry in manifest.entries
        )
        source_resolved = tuple(
            round(image.resolved_duration_seconds * 1_000)
            if image.resolved_duration_seconds is not None
            else None
            for image in ordered_images
        )
        legacy_completed_manifest = (
            all(value is None for value in manifest_resolved)
            and all(
                entry.status is VideoClipEntryStatus.STORED
                for entry in manifest.entries
            )
        )
        if (
            manifest.source_image_manifest_artifact_id != source.artifact_id
            or manifest.source_image_manifest_sha256 != source.sha256
            or manifest.source_image_manifest_schema_version != source.schema_version
            or manifest.configuration_fingerprint != self._configuration.fingerprint()
            or tuple(entry.visual_asset_id for entry in manifest.entries)
            != tuple(image.visual_asset_id for image in ordered_images)
            or (manifest_resolved != source_resolved and not legacy_completed_manifest)
        ):
            raise VideoClipIntegrityError("video clip manifest source or configuration changed")

    async def _recover_optional(
        self,
        entry: ProductionVideoClipEntry,
        image: VerifiedSourceImage,
        source: ReadImageAcquisitionManifest,
    ) -> ProductionVideoClipAsset | None:
        try:
            resolved = await self._store.resolve(
                job_id=source.job_id,
                visual_asset_id=image.visual_asset_id,
            )
        except (VideoClipNotFoundError, VideoClipIntegrityError, VideoClipConflictError):
            return None
        return self._validate_recovered(resolved.asset, entry, image, source)

    async def _recover_required(
        self,
        entry: ProductionVideoClipEntry,
        image: VerifiedSourceImage,
        source: ReadImageAcquisitionManifest,
    ) -> ProductionVideoClipAsset:
        recovered = await self._recover_optional(entry, image, source)
        if recovered is None:
            raise VideoClipIntegrityError("stored video clip cannot be recovered")
        return recovered

    def _validate_recovered(
        self,
        asset: ProductionVideoClipAsset,
        entry: ProductionVideoClipEntry,
        image: VerifiedSourceImage,
        source: ReadImageAcquisitionManifest,
    ) -> ProductionVideoClipAsset:
        metadata = asset.metadata
        expected_width, expected_height = self._configuration.output_dimensions(
            image.width, image.height
        )
        if (
            asset.asset_id != f"video-{image.visual_asset_id}"
            or asset.scene_id != image.scene_id
            or asset.shot_id != image.shot_id
            or asset.width != expected_width
            or asset.height != expected_height
            or (
                entry.requested_duration_seconds is not None
                and abs(asset.duration_seconds - entry.requested_duration_seconds) > 0.08
            )
            or abs(asset.frame_rate - self._configuration.frame_rate) > 0.01
            or metadata.source_image_manifest_artifact_id != source.artifact_id
            or metadata.source_image_manifest_sha256 != source.sha256
            or metadata.source_image_artifact_id != image.artifact_id
            or metadata.source_image_sha256 != image.sha256
            or metadata.source_visual_asset_id != image.visual_asset_id
            or metadata.configuration_fingerprint != self._configuration.fingerprint()
            or (
                entry.status is VideoClipEntryStatus.STORED
                and (
                    entry.sha256 != asset.sha256
                    or entry.size_bytes != asset.size_bytes
                    or entry.frame_count != asset.frame_count
                )
            )
        ):
            raise VideoClipIntegrityError(
                "recovered video clip provenance differs from current source"
            )
        return asset

    def _provider_request(
        self,
        command: StageCommand,
        context: StageContext,
        image: VerifiedSourceImage,
    ) -> VideoClipProviderRequest:
        width, height = self._configuration.output_dimensions(image.width, image.height)
        return VideoClipProviderRequest(
            job_id=command.job_id,
            command_id=command.command_id,
            correlation_id=context.correlation_id,
            attempt_number=command.attempt_number,
            visual_asset_id=image.visual_asset_id,
            source_image_artifact_id=image.artifact_id,
            source_image_sha256=image.sha256,
            source_image_mime_type=image.mime_type,
            source_image_size_bytes=image.size_bytes,
            source_image_width=image.width,
            source_image_height=image.height,
            source_role=image.role,
            source_metadata=image.metadata,
            source_image_content=image.content,
            duration_seconds=(
                image.resolved_duration_seconds
                or image.planned_duration_seconds
                or self._configuration.duration_seconds
            ),
            frame_rate=self._configuration.frame_rate,
            width=width,
            height=height,
            configuration=self._configuration,
            fingerprint=self._configuration.fingerprint(),
        )

    def _write_request(
        self,
        *,
        source: ReadImageAcquisitionManifest,
        image: VerifiedSourceImage,
        provider_request: VideoClipProviderRequest,
        response: VideoClipProviderResponse,
    ) -> VideoClipWriteRequest:
        width, height = self._configuration.output_dimensions(image.width, image.height)
        simulated = bool(response.metadata.get("simulated", False))
        attributes = {**response.metadata, "simulated": simulated}
        if simulated:
            attributes["animation_recipe"] = self._recipe_builder.build(image.visual_asset_id)
        return VideoClipWriteRequest(
            job_id=source.job_id,
            visual_asset_id=image.visual_asset_id,
            scene_id=image.scene_id,
            shot_id=image.shot_id,
            role=VisualAssetRole(image.role),
            expected_width=width,
            expected_height=height,
            expected_duration_seconds=provider_request.duration_seconds,
            expected_frame_rate=self._configuration.frame_rate,
            metadata=VideoClipMetadata(
                source_image_manifest_artifact_id=source.artifact_id,
                source_image_manifest_sha256=source.sha256,
                source_image_artifact_id=image.artifact_id,
                source_image_binary_asset_id=image.binary_asset_id,
                source_image_sha256=image.sha256,
                source_visual_asset_id=image.visual_asset_id,
                source_scene_id=image.scene_id,
                source_shot_id=image.shot_id,
                configuration_fingerprint=self._configuration.fingerprint(),
                provider=response.provider,
                requested_model=response.requested_model,
                reported_model=response.reported_model,
                deterministic=bool(response.metadata.get("deterministic", simulated)),
                attributes=attributes,
            ),
        )

    @staticmethod
    def _stored_entry(
        *,
        entry: ProductionVideoClipEntry,
        asset: ProductionVideoClipAsset,
        response: VideoClipProviderResponse | None,
        recovered: bool,
    ) -> ProductionVideoClipEntry:
        remote = _remote_entry_fields(response.metadata if response else asset.metadata.attributes)
        values = entry.model_dump(mode="python")
        values.update(
            {
                "status": VideoClipEntryStatus.STORED,
                "video_binary_asset_id": asset.asset_id,
                "video_artifact_id": _video_artifact_id(asset.job_id, entry.visual_asset_id),
                "storage_path": asset.storage_path,
                "mime_type": asset.mime_type,
                "extension": asset.extension,
                "sha256": asset.sha256,
                "size_bytes": asset.size_bytes,
                "width": asset.width,
                "height": asset.height,
                "duration_seconds": asset.duration_seconds,
                "frame_rate": asset.frame_rate,
                "frame_count": asset.frame_count,
                "video_codec": asset.video_codec,
                "audio_codec": None,
                "has_audio": False,
                "video_adaptation": _video_adaptation(
                    (
                        entry.resolved_scene_duration_ms / 1_000
                        if entry.resolved_scene_duration_ms is not None
                        else entry.planned_duration_seconds
                    ),
                    asset.duration_seconds,
                ),
                "provider": response.provider if response else asset.metadata.provider,
                "requested_model": (
                    response.requested_model if response else asset.metadata.requested_model
                ),
                "reported_model": (
                    response.reported_model if response else asset.metadata.reported_model
                ),
                "provider_request_id": response.request_id if response else None,
                "latency_ms": response.latency_ms if response else 0,
                "cost_usd": response.cost_usd if response else None,
                "error_code": None,
                **remote,
                "metadata": {
                    "recovered": recovered,
                    "simulated": asset.metadata.attributes.get("simulated", False),
                    "deterministic": asset.metadata.deterministic,
                },
            }
        )
        return ProductionVideoClipEntry.model_validate(values)

    async def _checkpoint_error(
        self,
        *,
        context: StageContext,
        manifest: ProductionVideoClipManifest,
        entry: ProductionVideoClipEntry,
        status: VideoClipEntryStatus,
        error_code: str,
        diagnostic_metadata: dict[str, object] | None = None,
    ) -> None:
        failed = entry.model_copy(
            update={
                "status": status,
                "error_code": error_code,
                "metadata": {**entry.metadata, **(diagnostic_metadata or {})},
            }
        )
        current = replace_manifest_entry(
            manifest,
            failed,
            status=(
                VideoClipManifestStatus.UNCERTAIN
                if status is VideoClipEntryStatus.UNCERTAIN
                else VideoClipManifestStatus.FAILED
            ),
        )
        await self._writer.checkpoint(context=context, previous=manifest, current=current)

    def _success(
        self,
        *,
        command: StageCommand,
        context: StageContext,
        source: ReadImageAcquisitionManifest,
        manifest: ProductionVideoClipManifest,
        stored_assets: dict[str, ProductionVideoClipAsset],
        started_at: datetime,
    ) -> StageExecutionOutput:
        artifacts: list[Artifact] = []
        for entry in manifest.entries:
            asset = stored_assets[entry.visual_asset_id]
            artifacts.append(
                Artifact(
                    artifact_id=_video_artifact_id(command.job_id, entry.visual_asset_id),
                    job_id=command.job_id,
                    artifact_type=ArtifactType.SOURCE_VIDEO_CLIP,
                    relative_path=asset.storage_path,
                    mime_type="video/mp4",
                    status=ArtifactStatus.READY,
                    size_bytes=asset.size_bytes,
                    sha256=asset.sha256,
                    duration_seconds=asset.duration_seconds,
                    width=asset.width,
                    height=asset.height,
                    provider=entry.provider,
                    model_version=entry.reported_model or entry.requested_model,
                    metadata={
                        "source_image_manifest_artifact_id": str(source.artifact_id),
                        "source_image_manifest_sha256": source.sha256,
                        "source_image_artifact_id": str(entry.source_image_artifact_id),
                        "source_image_binary_asset_id": (entry.source_image_binary_asset_id),
                        "source_image_sha256": entry.source_image_sha256,
                        "source_visual_asset_id": entry.visual_asset_id,
                        "source_scene_id": entry.source_scene_id,
                        "source_shot_id": entry.source_shot_id,
                        "role": entry.role.value,
                        "generation_mode": entry.generation_mode.value,
                        "planned_duration_seconds": entry.planned_duration_seconds,
                        "resolved_scene_duration_ms": entry.resolved_scene_duration_ms,
                        "requested_duration_seconds": entry.requested_duration_seconds,
                        "duration_seconds": entry.duration_seconds,
                        "video_adaptation": entry.video_adaptation,
                        "frame_rate": entry.frame_rate,
                        "frame_count": entry.frame_count,
                        "video_codec": entry.video_codec,
                        "has_audio": False,
                        "provider": entry.provider,
                        "requested_model": entry.requested_model,
                        "reported_model": entry.reported_model,
                        "provider_request_id": entry.provider_request_id,
                        "latency_ms": entry.latency_ms,
                        "cost_usd": (str(entry.cost_usd) if entry.cost_usd is not None else None),
                        "simulated": entry.metadata.get("simulated", False),
                        "deterministic": entry.metadata.get("deterministic", False),
                        "recovered": entry.metadata.get("recovered", False),
                        "remote_job_id": entry.remote_job_id,
                        "remote_generation_id": entry.remote_generation_id,
                        "remote_status": (
                            entry.remote_status.value if entry.remote_status is not None else None
                        ),
                        "remote_poll_attempts": entry.remote_poll_attempts,
                        "reported_cost_usd": (
                            str(entry.reported_cost_usd)
                            if entry.reported_cost_usd is not None
                            else None
                        ),
                        "estimated_cost_usd": (
                            str(entry.estimated_cost_usd)
                            if entry.estimated_cost_usd is not None
                            else None
                        ),
                        "pricing_sku": entry.pricing_sku,
                        "prompt_sha256": entry.prompt_sha256,
                        "capability_snapshot_hash": (entry.capability_snapshot_hash),
                        "publication_provider": entry.publication_provider,
                        "provider_request_fingerprint": (entry.provider_request_fingerprint),
                        "configuration_fingerprint": (manifest.configuration_fingerprint),
                    },
                )
            )
        content = serialize_video_clip_manifest(manifest)
        artifacts.append(
            Artifact(
                artifact_id=_manifest_artifact_id(command.job_id, command.attempt_number),
                job_id=command.job_id,
                artifact_type=ArtifactType.PRODUCTION_VIDEO_CLIP_MANIFEST,
                relative_path=video_clip_manifest_relative_path(context),
                mime_type="application/json",
                status=ArtifactStatus.READY,
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
                provider=self._configuration.provider,
                model_version=self._configuration.model,
                metadata={
                    "schema_version": manifest.schema_version,
                    "source_image_manifest_artifact_id": str(source.artifact_id),
                    "source_image_manifest_sha256": source.sha256,
                    "entry_count": len(manifest.entries),
                    "stored_count": manifest.summary.stored,
                    "requested_durations_seconds": [
                        entry.requested_duration_seconds for entry in manifest.entries
                    ],
                    "estimated_cost_usd": str(
                        sum(
                            (
                                entry.estimated_cost_usd or Decimal("0")
                                for entry in manifest.entries
                            ),
                            Decimal("0"),
                        )
                    ),
                    "checkpointed": True,
                    "configuration_fingerprint": (manifest.configuration_fingerprint),
                },
            )
        )
        logger.info(
            "video clip generation stage completed",
            extra={
                "job_id": str(command.job_id),
                "command_id": str(command.command_id),
                "attempt": command.attempt_number,
                "provider": self._configuration.provider,
                "requested_model": self._configuration.model,
                "clip_count": len(stored_assets),
                "simulated": self._configuration.provider == "simulated",
            },
        )
        return StageExecutionOutput(
            result=StageResult(
                command_id=command.command_id,
                job_id=command.job_id,
                stage=command.stage,
                outcome=StageOutcome.SUCCEEDED,
                started_at=started_at,
                finished_at=self._aware_now(),
                progress_percent=100,
                output_artifact_ids=tuple(item.artifact_id for item in artifacts),
                metadata={
                    "handler": type(self).__name__,
                    "provider": self._configuration.provider,
                    "requested_model": self._configuration.model,
                    "clip_count": len(stored_assets),
                    "checkpointed": True,
                    "simulated": self._configuration.provider == "simulated",
                },
            ),
            artifacts=tuple(artifacts),
        )

    def _failure(
        self,
        command: StageCommand,
        started_at: datetime,
        outcome: StageOutcome,
        error_code: str,
        *,
        retry_after_seconds: float | None = None,
        diagnostic_metadata: dict[str, object] | None = None,
    ) -> StageExecutionOutput:
        logger.warning(
            "video clip generation stage did not complete",
            extra={
                "job_id": str(command.job_id),
                "command_id": str(command.command_id),
                "attempt": command.attempt_number,
                "outcome": outcome.value,
                "error_code": error_code,
            },
        )
        return StageExecutionOutput(
            result=StageResult(
                command_id=command.command_id,
                job_id=command.job_id,
                stage=command.stage,
                outcome=outcome,
                started_at=started_at,
                finished_at=self._aware_now(),
                progress_percent=0,
                error_code=error_code,
                error_message="Video clip generation stage could not complete",
                retry_after_seconds=retry_after_seconds,
                metadata={
                    "handler": type(self).__name__,
                    "error_category": error_code,
                    **(diagnostic_metadata or {}),
                },
            )
        )

    def _request_diagnostic_metadata(
        self,
        *,
        image: VerifiedSourceImage,
        phase: str,
        diagnostic_code: str,
    ) -> dict[str, object]:
        return {
            "phase": phase,
            "diagnostic_code": diagnostic_code,
            "requested_model": self._configuration.model,
            "requested_duration_seconds": self._configuration.duration_seconds,
            "requested_resolution": self._configuration.resolution,
            "requested_aspect_ratio": self._configuration.aspect_ratio(
                image.width, image.height
            ),
            "generate_audio": self._configuration.generate_audio,
            "source_image_sha256": image.sha256,
        }

    def _provider_diagnostic_metadata(
        self,
        error: OpenRouterVideoError,
        *,
        image: VerifiedSourceImage,
    ) -> dict[str, object]:
        metadata = self._request_diagnostic_metadata(
            image=image,
            phase=error.diagnostic_phase or "pre_submission",
            diagnostic_code=error.diagnostic_code or "unknown_pre_submission_error",
        )
        allowed = {
            "capability_endpoint_status",
            "capability_model_found",
            "pricing_sku",
            "estimated_cost_usd",
            "max_estimated_cost_usd",
            "estimated_job_cost_usd",
            "max_estimated_job_cost_usd",
            "planned_request_count",
            "existing_request_count",
            "max_requests_per_job",
            "publication_id",
            "source_asset_provider",
            "source_asset_model",
            "source_asset_simulated",
        }
        metadata.update(
            {
                key: value
                for key, value in error.diagnostic_metadata.items()
                if key in allowed
                and (value is None or isinstance(value, (str, int, float, bool)))
            }
        )
        if error.http_status is not None:
            metadata["capability_endpoint_status"] = error.http_status
        return metadata

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("video clip clock must be timezone-aware")
        return value


def _entry_for(
    manifest: ProductionVideoClipManifest, visual_asset_id: str
) -> ProductionVideoClipEntry:
    return next(entry for entry in manifest.entries if entry.visual_asset_id == visual_asset_id)


def _pre_submission_error_code(error: OpenRouterVideoError) -> str | None:
    phase = error.diagnostic_phase
    if phase == "publication":
        return "video_clip_publication_error"
    if phase == "pricing_discovery":
        return "video_clip_pricing_error"
    if phase in {"cost_estimation", "cost_authorization", "aggregate_cost_authorization"}:
        return "video_clip_cost_policy"
    if phase in {"capability_discovery", "capability_contract"}:
        return "video_clip_capability_error"
    if phase in {"request_construction", "configuration"}:
        return "video_clip_request_invalid"
    if phase == "source_validation":
        return "video_clip_source_invalid"
    if phase == "pre_submission":
        return "video_clip_provider_contract"
    return None


def _video_artifact_id(job_id: UUID, visual_asset_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"orion:{job_id}:source-video-clip:{visual_asset_id}")


def _manifest_artifact_id(job_id: UUID, attempt_number: int) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"orion:{job_id}:video-clip-generation-manifest:{attempt_number}",
    )


def _remote_entry_fields(metadata: dict[str, object]) -> dict[str, object]:
    if metadata.get("remote_provider") != "openrouter":
        return {}
    status = metadata.get("remote_status")
    return {
        "remote_provider": "openrouter",
        "remote_job_id": metadata.get("remote_job_id"),
        "remote_generation_id": metadata.get("remote_generation_id"),
        "remote_status": VideoClipRemoteStatus(str(status)),
        "remote_submitted_at": _metadata_datetime(metadata.get("remote_submitted_at")),
        "remote_last_polled_at": _metadata_datetime(metadata.get("remote_last_polled_at")),
        "remote_poll_attempts": metadata.get("remote_poll_attempts"),
        "remote_terminal_at": _metadata_datetime(metadata.get("remote_terminal_at")),
        "remote_content_available": metadata.get("remote_content_available"),
        "estimated_cost_usd": metadata.get("estimated_cost_usd"),
        "reported_cost_usd": metadata.get("reported_cost_usd"),
        "pricing_snapshot_at": _metadata_datetime(metadata.get("pricing_snapshot_at")),
        "pricing_sku": metadata.get("pricing_sku"),
        "prompt_sha256": metadata.get("prompt_sha256"),
        "source_publication_id": metadata.get("source_publication_id"),
        "source_publication_expires_at": _metadata_datetime(
            metadata.get("source_publication_expires_at")
        ),
        "publication_provider": metadata.get("publication_provider"),
        "provider_request_fingerprint": metadata.get("provider_request_fingerprint"),
        "capability_snapshot_hash": metadata.get("capability_snapshot_hash"),
        "remote_url_metadata": {},
    }


def _metadata_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise VideoClipProviderResponseException("provider remote timestamp is invalid")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise VideoClipProviderResponseException("provider remote timestamp is not timezone-aware")
    return result


def _video_adaptation(
    planned_duration_seconds: float | None,
    actual_duration_seconds: float,
) -> str:
    if planned_duration_seconds is None:
        return "none"
    difference = actual_duration_seconds - planned_duration_seconds
    if abs(difference) <= 0.001:
        return "none"
    return "trim" if difference > 0 else "loop"
