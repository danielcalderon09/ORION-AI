"""Durable sequential ACQUIRING_ASSETS handler."""

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome, StageResult
from backend.src.production.binary_assets.exceptions import (
    BinaryAssetConfigurationError,
    BinaryAssetConflictError,
    BinaryAssetCorruptError,
    BinaryAssetError,
    BinaryAssetHashError,
    BinaryAssetIOError,
    BinaryAssetLinkError,
    BinaryAssetMetadataError,
    BinaryAssetMimeError,
    BinaryAssetNotFoundError,
    BinaryAssetPathError,
    BinaryAssetSizeError,
)
from backend.src.production.binary_assets.models import (
    BinaryAssetRole,
    BinaryAssetWriteRequest,
    ProductionBinaryAsset,
    ProductionBinaryAssetMetadata,
)
from backend.src.production.binary_assets.ports import (
    BinaryAssetReader,
    BinaryAssetWriter,
)
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.enums import (
    ArtifactStatus,
    ArtifactType,
    ProductionStage,
)
from backend.src.production.image_acquisition.configuration import (
    ImageAcquisitionConfiguration,
    OpenRouterImageBillablePolicy,
)
from backend.src.production.image_acquisition.diagnostics import (
    ImageDiagnosticMetadata,
    ImageDiagnosticSubtype,
)
from backend.src.production.image_acquisition.exceptions import (
    ImageAcquisitionManifestError,
    ImageAcquisitionProviderAuthenticationException,
    ImageAcquisitionProviderConfigurationException,
    ImageAcquisitionProviderContractException,
    ImageAcquisitionProviderError,
    ImageAcquisitionProviderModelException,
    ImageAcquisitionProviderPolicyException,
    ImageAcquisitionProviderRateLimitException,
    ImageAcquisitionProviderResponseException,
    ImageAcquisitionProviderTimeoutException,
    ImageAcquisitionProviderUnavailableException,
    ImageAcquisitionProviderUncertainException,
    ImageAcquisitionUnsupportedAssetException,
    ImageAcquisitionValidationError,
    ProductionVisualAssetPlanReadError,
    ProductionVisualAssetPlanTransientReadException,
)
from backend.src.production.image_acquisition.manifest_writer import (
    image_acquisition_manifest_relative_path,
)
from backend.src.production.image_acquisition.models import (
    ImageAcquisitionEntryStatus,
    ImageAcquisitionManifestStatus,
    OpenRouterImageRequestStatus,
    ProductionImageAcquisitionEntry,
    ProductionImageAcquisitionManifest,
    replace_manifest_entry,
    summarize_entries,
)
from backend.src.production.image_acquisition.ports import (
    ImageAcquisitionManifestWriter,
    ImageAcquisitionProvider,
    ImageAcquisitionProviderRequest,
    ImageAcquisitionProviderResponse,
    ProductionVisualAssetPlanReader,
    ReadProductionVisualAssetPlan,
)
from backend.src.production.image_acquisition.prompt_builder import (
    ImageGenerationPromptBuilder,
)
from backend.src.production.image_acquisition.serialization import (
    serialize_image_acquisition_manifest,
)
from backend.src.production.runtime.context import StageContext
from backend.src.production.runtime.runtime_models import StageExecutionOutput
from backend.src.production.visual_asset_planning.models import (
    AssetKind,
    GenerationMode,
    ProductionVisualAssetSpec,
    VisualAssetRole,
)

_EXTENSION = {"png": "png", "jpeg": "jpg", "webp": "webp"}
_MIME = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}
_FORMAT_BY_MIME = {mime_type: output_format for output_format, mime_type in _MIME.items()}
_ROLE = {
    VisualAssetRole.PRIMARY: BinaryAssetRole.PRIMARY,
    VisualAssetRole.SUPPORTING: BinaryAssetRole.SUPPORTING,
    VisualAssetRole.REFERENCE: BinaryAssetRole.REFERENCE,
}
logger = logging.getLogger(__name__)


class ImageAcquisitionHandler:
    supported_stages = frozenset({ProductionStage.ACQUIRING_ASSETS})

    def __init__(
        self,
        *,
        plan_reader: ProductionVisualAssetPlanReader,
        provider: ImageAcquisitionProvider,
        manifest_writer: ImageAcquisitionManifestWriter,
        binary_reader: BinaryAssetReader,
        binary_writer: BinaryAssetWriter,
        configuration: ImageAcquisitionConfiguration,
        provider_name: str,
        requested_model: str | None,
        prompt_builder: ImageGenerationPromptBuilder,
        clock: Callable[[], datetime],
        billable_policy: OpenRouterImageBillablePolicy | None = None,
    ) -> None:
        self._plan_reader = plan_reader
        self._provider = provider
        self._manifest_writer = manifest_writer
        self._binary_reader = binary_reader
        self._binary_writer = binary_writer
        self._configuration = configuration
        self._provider_name = provider_name
        self._requested_model = requested_model
        self._prompt_builder = prompt_builder
        self._clock = clock
        self._billable_policy = billable_policy

    async def execute(
        self,
        command: StageCommand,
        context: StageContext,
    ) -> StageExecutionOutput:
        if command.stage is not ProductionStage.ACQUIRING_ASSETS:
            raise ValueError("ImageAcquisitionHandler only supports ACQUIRING_ASSETS")
        started_at = self._aware_now()
        logger.info(
            "image acquisition stage started",
            extra={
                "job_id": str(command.job_id),
                "command_id": str(command.command_id),
                "attempt": command.attempt_number,
                "provider": self._provider_name,
                "requested_model": self._requested_model,
            },
        )
        try:
            source = await self._plan_reader.read_for_image_acquisition(context=context)
            assets = tuple(
                sorted(
                    source.visual_asset_plan.assets,
                    key=lambda item: item.asset_id,
                )
            )
            self._validate_supported(assets)
            manifest = await self._manifest_writer.read_existing(context=context)
            if manifest is None:
                manifest = self._initial_manifest(
                    source=source,
                    assets=assets,
                    attempt_number=command.attempt_number,
                )
                await self._manifest_writer.create(
                    context=context,
                    manifest=manifest,
                )
            else:
                self._validate_manifest_source(manifest, source, assets)

            stored_assets: dict[str, ProductionBinaryAsset] = {}
            for asset_spec in assets:
                entry = _entry_for(manifest, asset_spec.asset_id)
                if entry.status is ImageAcquisitionEntryStatus.STORED:
                    binary = await self._verify_entry(
                        entry=entry,
                        spec=asset_spec,
                        source=source,
                    )
                    recovered_entry = entry.model_copy(
                        update={
                            "metadata": {
                                **entry.metadata,
                                "recovered": True,
                            }
                        }
                    )
                    current = replace_manifest_entry(manifest, recovered_entry)
                    await self._manifest_writer.checkpoint(
                        context=context,
                        previous=manifest,
                        current=current,
                    )
                    manifest = current
                    stored_assets[asset_spec.asset_id] = binary
                    self._log_stored(command, recovered_entry, binary)
                    continue
                if entry.status is ImageAcquisitionEntryStatus.GENERATING:
                    recovered = await self._recover_uncheckpointed_binary(
                        manifest=manifest,
                        entry=entry,
                        spec=asset_spec,
                        source=source,
                        context=context,
                    )
                    if recovered is None:
                        uncertain = entry.model_copy(
                            update={
                                "status": ImageAcquisitionEntryStatus.UNCERTAIN,
                                "error_code": "external_result_uncertain",
                            }
                        )
                        current = replace_manifest_entry(
                            manifest,
                            uncertain,
                            status=ImageAcquisitionManifestStatus.UNCERTAIN,
                        )
                        await self._manifest_writer.checkpoint(
                            context=context,
                            previous=manifest,
                            current=current,
                        )
                        return self._failure(
                            command,
                            started_at,
                            StageOutcome.NEEDS_USER_ACTION,
                            "image_acquisition_result_uncertain",
                        )
                    manifest, binary = recovered
                    stored_assets[asset_spec.asset_id] = binary
                    self._log_stored(
                        command,
                        _entry_for(manifest, asset_spec.asset_id),
                        binary,
                    )
                    continue
                if entry.status is ImageAcquisitionEntryStatus.UNCERTAIN:
                    return self._failure(
                        command,
                        started_at,
                        StageOutcome.NEEDS_USER_ACTION,
                        "image_acquisition_result_uncertain",
                    )
                if entry.status in {
                    ImageAcquisitionEntryStatus.FAILED_PERMANENT,
                    ImageAcquisitionEntryStatus.FAILED_TRANSIENT,
                }:
                    outcome = (
                        StageOutcome.FAILED_TRANSIENT
                        if entry.status is ImageAcquisitionEntryStatus.FAILED_TRANSIENT
                        else StageOutcome.FAILED_PERMANENT
                    )
                    return self._failure(
                        command,
                        started_at,
                        outcome,
                        entry.error_code or "image_acquisition_failed",
                        retry_after_seconds=1.0
                        if outcome is StageOutcome.FAILED_TRANSIENT
                        else None,
                    )

                reused = await self._try_reuse_binary(
                    spec=asset_spec,
                    source=source,
                )
                if reused is not None:
                    stored_entry = self._stored_entry_from_binary(
                        entry=entry,
                        binary=reused,
                        response=None,
                        recovered=True,
                    )
                    current = replace_manifest_entry(manifest, stored_entry)
                    await self._manifest_writer.checkpoint(
                        context=context,
                        previous=manifest,
                        current=current,
                    )
                    manifest = current
                    stored_assets[asset_spec.asset_id] = reused
                    self._log_stored(command, stored_entry, reused)
                    continue

                if self._provider_name == "openrouter":
                    if entry.request_status in {
                        OpenRouterImageRequestStatus.SUBMITTING,
                        OpenRouterImageRequestStatus.COMPLETED,
                        OpenRouterImageRequestStatus.UNCERTAIN,
                    }:
                        await self._checkpoint_error(
                            context=context,
                            manifest=manifest,
                            entry=entry,
                            status=ImageAcquisitionEntryStatus.UNCERTAIN,
                            error_code="external_result_uncertain",
                        )
                        return self._failure(
                            command,
                            started_at,
                            StageOutcome.NEEDS_USER_ACTION,
                            "image_acquisition_result_uncertain",
                        )
                    prompt = self._prompt_builder.build(
                        self._provider_request(command, context, asset_spec)
                    )
                    fingerprint = self._remote_fingerprint(asset_spec, prompt.sha256)
                    prepared = entry.model_copy(
                        update={
                            "request_status": OpenRouterImageRequestStatus.PREPARED,
                            "request_fingerprint": fingerprint,
                            "fresh_submission_permitted": True,
                        }
                    )
                    current = replace_manifest_entry(manifest, prepared)
                    await self._manifest_writer.checkpoint(
                        context=context, previous=manifest, current=current
                    )
                    manifest = current
                    self._authorize_billable(manifest, prepared)
                    entry = prepared
                generating = entry.model_copy(
                    update={
                        "status": ImageAcquisitionEntryStatus.GENERATING,
                        "request_status": (
                            OpenRouterImageRequestStatus.SUBMITTING
                            if self._provider_name == "openrouter"
                            else None
                        ),
                        "fresh_submission_permitted": (
                            False if self._provider_name == "openrouter" else None
                        ),
                    }
                )
                current = replace_manifest_entry(manifest, generating)
                await self._manifest_writer.checkpoint(
                    context=context,
                    previous=manifest,
                    current=current,
                )
                manifest = current
                try:
                    response = await self._provider.generate_image(
                        self._provider_request(command, context, asset_spec)
                    )
                    if self._provider_name == "openrouter":
                        responded = generating.model_copy(
                            update={
                                "request_status": OpenRouterImageRequestStatus.COMPLETED,
                                "fresh_submission_permitted": False,
                                "requested_model": response.requested_model,
                                "reported_model": response.reported_model,
                                "provider_request_id": response.request_id,
                                "input_tokens": response.input_tokens,
                                "output_tokens": response.output_tokens,
                                "total_tokens": response.total_tokens,
                                "cost_usd": response.cost_usd,
                                "http_status": response.http_status,
                                "latency_ms": response.latency_ms,
                                "finish_reason": response.finish_reason,
                                "diagnostic_metadata": _response_diagnostic(response),
                            }
                        )
                        current = replace_manifest_entry(manifest, responded)
                        await self._manifest_writer.checkpoint(
                            context=context, previous=manifest, current=current
                        )
                        manifest = current
                        generating = responded
                    if len(response.images) != 1:
                        raise ImageAcquisitionProviderContractException(
                            "image provider must return exactly one image",
                            diagnostic_subtype=ImageDiagnosticSubtype.MULTIPLE_IMAGES,
                            diagnostic_metadata=generating.diagnostic_metadata,
                        )
                    payload = response.images[0]
                    expected_mime = _MIME[self._configuration.output_format]
                    durable_mime = payload.mime_type or expected_mime
                    durable_output_format = _FORMAT_BY_MIME.get(durable_mime)
                    if durable_output_format is None:
                        raise ImageAcquisitionProviderContractException(
                            "provider returned an unsupported image format",
                            diagnostic_subtype=(
                                ImageDiagnosticSubtype.UNSUPPORTED_IMAGE_FORMAT
                            ),
                            diagnostic_metadata=generating.diagnostic_metadata,
                            validation_error_code="unsupported_image_format",
                            validation_error_path="images[0].mime_type",
                            validation_error_message=(
                                "provider returned an unsupported image format"
                            ),
                        )
                    prompt = self._prompt_builder.build(
                        self._provider_request(command, context, asset_spec)
                    )
                    binary = await self._binary_writer.write(
                        request=BinaryAssetWriteRequest(
                            asset_id=_binary_asset_id(asset_spec.asset_id),
                            job_id=command.job_id,
                            scene_id=asset_spec.source_scene_id,
                            shot_id=asset_spec.source_shot_id,
                            asset_role=_ROLE[asset_spec.role],
                            mime_type=durable_mime,
                            extension=_EXTENSION[durable_output_format],
                            expected_width=(
                                _safe_image_dimension(payload.provider_metadata.get("width"))
                                if self._provider_name == "openrouter"
                                else asset_spec.width
                            ),
                            expected_height=(
                                _safe_image_dimension(payload.provider_metadata.get("height"))
                                if self._provider_name == "openrouter"
                                else asset_spec.height
                            ),
                            metadata=ProductionBinaryAssetMetadata(
                                source_visual_asset_id=asset_spec.asset_id,
                                source_visual_asset_plan_artifact_id=source.artifact_id,
                                provider=response.provider,
                                model_version=(response.reported_model or response.requested_model),
                                deterministic=response.metadata.get("deterministic"),
                                attributes={
                                    "source_visual_asset_plan_sha256": source.sha256,
                                    "prompt_version": prompt.version,
                                    "prompt_sha256": prompt.sha256,
                                    "acquisition_configuration_sha256": (
                                        self._configuration_sha256()
                                    ),
                                    "configured_provider": self._provider_name,
                                    "configured_model": self._requested_model,
                                    "video_visual_subject": asset_spec.visual_subject,
                                    "video_environment": asset_spec.environment,
                                    "video_action": asset_spec.composition.action,
                                    "video_camera_movement": (
                                        asset_spec.camera_intent.movement
                                    ),
                                    "video_camera_framing": (
                                        asset_spec.camera_intent.framing
                                    ),
                                    "simulated": response.metadata.get(
                                        "simulated",
                                        False,
                                    ),
                                },
                            ),
                        ),
                        content=payload.content,
                    )
                except asyncio.CancelledError:
                    await self._checkpoint_error(
                        context=context,
                        manifest=manifest,
                        entry=generating,
                        status=ImageAcquisitionEntryStatus.UNCERTAIN,
                        error_code="external_result_uncertain",
                        diagnostic_subtype=ImageDiagnosticSubtype.UNCERTAIN_TRANSPORT,
                    )
                    raise
                except (
                    ImageAcquisitionProviderUncertainException,
                    ImageAcquisitionProviderTimeoutException,
                ) as exc:
                    await self._checkpoint_error(
                        context=context,
                        manifest=manifest,
                        entry=generating,
                        status=ImageAcquisitionEntryStatus.UNCERTAIN,
                        error_code=self._error_code(exc),
                        error=exc,
                    )
                    return self._failure(
                        command,
                        started_at,
                        StageOutcome.NEEDS_USER_ACTION,
                        "image_acquisition_result_uncertain",
                    )
                except BinaryAssetError as exc:
                    await self._checkpoint_error(
                        context=context,
                        manifest=manifest,
                        entry=generating,
                        status=ImageAcquisitionEntryStatus.FAILED_PERMANENT,
                        error_code=self._error_code(exc),
                        error=exc,
                        diagnostic_subtype=_binary_diagnostic_subtype(exc),
                    )
                    raise
                except (ValidationError, TypeError, ValueError) as exc:
                    await self._checkpoint_error(
                        context=context,
                        manifest=manifest,
                        entry=generating,
                        status=ImageAcquisitionEntryStatus.FAILED_PERMANENT,
                        error_code=self._error_code(exc),
                        error=exc,
                        diagnostic_subtype=(ImageDiagnosticSubtype.BINARY_ASSET_VALIDATION),
                    )
                    raise
                except ImageAcquisitionProviderError as exc:
                    await self._checkpoint_error(
                        context=context,
                        manifest=manifest,
                        entry=generating,
                        status=ImageAcquisitionEntryStatus.FAILED_PERMANENT,
                        error_code=self._error_code(exc),
                        error=exc,
                    )
                    raise
                stored_entry = self._stored_entry_from_binary(
                    entry=generating,
                    binary=binary,
                    response=response,
                    recovered=False,
                )
                current = replace_manifest_entry(manifest, stored_entry)
                await self._manifest_writer.checkpoint(
                    context=context,
                    previous=manifest,
                    current=current,
                )
                manifest = current
                stored_assets[asset_spec.asset_id] = binary
                self._log_stored(command, stored_entry, binary)

            completed = manifest.model_copy(
                update={"status": ImageAcquisitionManifestStatus.COMPLETED}
            )
            completed = ProductionImageAcquisitionManifest.model_validate(
                completed.model_dump(mode="python")
            )
            await self._manifest_writer.finalize(
                context=context,
                previous=manifest,
                current=completed,
            )
        except ProductionVisualAssetPlanTransientReadException as exc:
            return self._failure(
                command,
                started_at,
                StageOutcome.FAILED_TRANSIENT,
                self._error_code(exc),
                retry_after_seconds=1.0,
            )
        except (
            ProductionVisualAssetPlanReadError,
            ImageAcquisitionUnsupportedAssetException,
            ImageAcquisitionValidationError,
            ImageAcquisitionManifestError,
            ImageAcquisitionProviderAuthenticationException,
            ImageAcquisitionProviderContractException,
            ImageAcquisitionProviderModelException,
            ImageAcquisitionProviderPolicyException,
            ImageAcquisitionProviderResponseException,
            ImageAcquisitionProviderError,
            BinaryAssetError,
            ValidationError,
            TypeError,
            ValueError,
        ) as exc:
            return self._failure(
                command,
                started_at,
                StageOutcome.FAILED_PERMANENT,
                self._error_code(exc),
                diagnostic_subtype=_diagnostic_subtype(exc),
            )
        return self._success(
            command=command,
            context=context,
            source=source,
            manifest=completed,
            stored_assets=stored_assets,
            started_at=started_at,
        )

    async def _try_reuse_binary(
        self,
        *,
        spec: ProductionVisualAssetSpec,
        source: ReadProductionVisualAssetPlan,
    ) -> ProductionBinaryAsset | None:
        try:
            result = await self._binary_reader.resolve(
                job_id=source.job_id,
                asset_id=_binary_asset_id(spec.asset_id),
                extension=_EXTENSION[self._configuration.output_format],
            )
        except BinaryAssetNotFoundError:
            return None
        except BinaryAssetError:
            raise
        return self._validate_binary_provenance(result.asset, spec, source)

    async def _verify_entry(
        self,
        *,
        entry: ProductionImageAcquisitionEntry,
        spec: ProductionVisualAssetSpec,
        source: ReadProductionVisualAssetPlan,
    ) -> ProductionBinaryAsset:
        assert entry.binary_asset_id is not None
        assert entry.extension is not None
        result = await self._binary_reader.resolve(
            job_id=source.job_id,
            asset_id=entry.binary_asset_id,
            extension=entry.extension,
        )
        binary = self._validate_binary_provenance(result.asset, spec, source)
        if (
            entry.sha256 != binary.sha256
            or entry.size_bytes != binary.size_bytes
            or entry.mime_type != binary.mime_type
            or entry.width != binary.width
            or entry.height != binary.height
            or entry.storage_path != binary.storage_path
        ):
            raise ImageAcquisitionValidationError(
                "manifest binary metadata differs from verified asset"
            )
        return binary

    async def _recover_uncheckpointed_binary(
        self,
        *,
        manifest: ProductionImageAcquisitionManifest,
        entry: ProductionImageAcquisitionEntry,
        spec: ProductionVisualAssetSpec,
        source: ReadProductionVisualAssetPlan,
        context: StageContext,
    ) -> tuple[ProductionImageAcquisitionManifest, ProductionBinaryAsset] | None:
        reused = await self._try_reuse_binary(spec=spec, source=source)
        if reused is None:
            return None
        stored = self._stored_entry_from_binary(
            entry=entry,
            binary=reused,
            response=None,
            recovered=True,
        )
        current = replace_manifest_entry(manifest, stored)
        await self._manifest_writer.checkpoint(
            context=context,
            previous=manifest,
            current=current,
        )
        return current, reused

    def _validate_binary_provenance(
        self,
        binary: ProductionBinaryAsset,
        spec: ProductionVisualAssetSpec,
        source: ReadProductionVisualAssetPlan,
    ) -> ProductionBinaryAsset:
        if (
            binary.scene_id != spec.source_scene_id
            or binary.shot_id != spec.source_shot_id
            or binary.width != spec.width
            or binary.height != spec.height
            or binary.metadata.source_visual_asset_id != spec.asset_id
            or binary.metadata.source_visual_asset_plan_artifact_id != source.artifact_id
            or binary.metadata.attributes.get("source_visual_asset_plan_sha256") != source.sha256
            or binary.metadata.attributes.get("acquisition_configuration_sha256")
            != self._configuration_sha256()
            or binary.metadata.attributes.get("configured_provider") != self._provider_name
            or binary.metadata.attributes.get("configured_model") != self._requested_model
        ):
            raise ImageAcquisitionValidationError(
                "binary asset provenance differs from visual asset plan"
            )
        return binary

    def _configuration_sha256(self) -> str:
        content = json.dumps(
            self._configuration.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    def _remote_fingerprint(self, asset: ProductionVisualAssetSpec, prompt_sha256: str) -> str:
        content = json.dumps(
            {
                "provider": self._provider_name,
                "model": self._requested_model,
                "visual_prompt_sha256": prompt_sha256,
                "scene_id": asset.source_scene_id,
                "shot_id": asset.source_shot_id,
                "visual_asset_id": asset.asset_id,
                "aspect_ratio": asset.aspect_ratio,
                "resolution": "1K",
                "reference_image_fingerprints": [],
                "provider_request_schema_version": "1.0.0",
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    def _authorize_billable(
        self,
        manifest: ProductionImageAcquisitionManifest,
        entry: ProductionImageAcquisitionEntry,
    ) -> None:
        policy = self._billable_policy
        if policy is None or not policy.allow_billable_requests:
            raise ImageAcquisitionProviderConfigurationException(
                "billable image requests are disabled"
            )
        if policy.estimated_cost_usd is None or policy.maximum_authorized_cost_usd is None:
            raise ImageAcquisitionProviderConfigurationException(
                "image cost authorization is missing"
            )
        submitted = sum(
            item.request_status
            in {
                OpenRouterImageRequestStatus.SUBMITTING,
                OpenRouterImageRequestStatus.COMPLETED,
                OpenRouterImageRequestStatus.FAILED,
                OpenRouterImageRequestStatus.UNCERTAIN,
            }
            for item in manifest.entries
        )
        if submitted >= policy.maximum_requests_per_job:
            raise ImageAcquisitionProviderConfigurationException(
                "image job request limit was reached"
            )
        if entry.request_status is not OpenRouterImageRequestStatus.PREPARED:
            raise ImageAcquisitionProviderConfigurationException(
                "durable prepared image checkpoint is required"
            )

    async def _checkpoint_error(
        self,
        *,
        context: StageContext,
        manifest: ProductionImageAcquisitionManifest,
        entry: ProductionImageAcquisitionEntry,
        status: ImageAcquisitionEntryStatus,
        error_code: str,
        error: Exception | None = None,
        diagnostic_subtype: ImageDiagnosticSubtype | None = None,
    ) -> None:
        remote_status = None
        fresh_submission = None
        if entry.request_status is not None:
            remote_status = (
                OpenRouterImageRequestStatus.UNCERTAIN
                if status is ImageAcquisitionEntryStatus.UNCERTAIN
                else OpenRouterImageRequestStatus.FAILED
            )
            fresh_submission = False
        error_http_status = getattr(error, "http_status", None)
        error_request_id = getattr(error, "provider_request_id", None)
        subtype = (
            diagnostic_subtype
            or getattr(error, "diagnostic_subtype", None)
            or _diagnostic_subtype(error)
        )
        validation_code, validation_path, validation_message = _validation_summary(error)
        diagnostic_metadata = getattr(error, "diagnostic_metadata", None)
        failed = entry.model_copy(
            update={
                "status": status,
                "error_code": error_code,
                "request_status": remote_status,
                "fresh_submission_permitted": fresh_submission,
                "http_status": (
                    error_http_status if error_http_status is not None else entry.http_status
                ),
                "provider_request_id": (
                    error_request_id if error_request_id is not None else entry.provider_request_id
                ),
                "requested_model": (
                    getattr(error, "requested_model", None)
                    or entry.requested_model
                    or self._requested_model
                ),
                "reported_model": (getattr(error, "reported_model", None) or entry.reported_model),
                "input_tokens": (
                    getattr(error, "input_tokens", None)
                    if getattr(error, "input_tokens", None) is not None
                    else entry.input_tokens
                ),
                "output_tokens": (
                    getattr(error, "output_tokens", None)
                    if getattr(error, "output_tokens", None) is not None
                    else entry.output_tokens
                ),
                "total_tokens": (
                    getattr(error, "total_tokens", None)
                    if getattr(error, "total_tokens", None) is not None
                    else entry.total_tokens
                ),
                "cost_usd": _durable_reported_cost(
                    getattr(error, "cost_usd", None),
                    entry.cost_usd,
                ),
                "latency_ms": (
                    getattr(error, "latency_ms", None)
                    if getattr(error, "latency_ms", None) is not None
                    else entry.latency_ms
                ),
                "finish_reason": (getattr(error, "finish_reason", None) or entry.finish_reason),
                "diagnostic_subtype": subtype,
                "validation_error_code": (
                    getattr(error, "validation_error_code", None) or validation_code
                ),
                "validation_error_path": (
                    getattr(error, "validation_error_path", None) or validation_path
                ),
                "validation_error_message": (
                    getattr(error, "validation_error_message", None) or validation_message
                ),
                "diagnostic_metadata": (
                    diagnostic_metadata
                    if isinstance(diagnostic_metadata, ImageDiagnosticMetadata)
                    else entry.diagnostic_metadata
                ),
            }
        )
        root_status = (
            ImageAcquisitionManifestStatus.FAILED
            if status is not ImageAcquisitionEntryStatus.UNCERTAIN
            else ImageAcquisitionManifestStatus.UNCERTAIN
        )
        current = replace_manifest_entry(
            manifest,
            failed,
            status=root_status,
        )
        await self._manifest_writer.checkpoint(
            context=context,
            previous=manifest,
            current=current,
        )

    def _initial_manifest(
        self,
        *,
        source: ReadProductionVisualAssetPlan,
        assets: tuple[ProductionVisualAssetSpec, ...],
        attempt_number: int,
    ) -> ProductionImageAcquisitionManifest:
        entries = tuple(
            ProductionImageAcquisitionEntry(
                visual_asset_id=asset.asset_id,
                scene_number=asset.scene_number,
                source_scene_id=asset.source_scene_id,
                shot_number=asset.shot_number,
                source_shot_id=asset.source_shot_id,
                role=asset.role,
                generation_mode=asset.generation_mode,
                status=ImageAcquisitionEntryStatus.PENDING,
                attempt_number=attempt_number,
            )
            for asset in assets
        )
        return ProductionImageAcquisitionManifest(
            source_visual_asset_plan_schema_version=source.schema_version,
            source_visual_asset_plan_artifact_id=source.artifact_id,
            source_visual_asset_plan_sha256=source.sha256,
            provider=self._provider_name,
            requested_model=self._requested_model,
            status=ImageAcquisitionManifestStatus.IN_PROGRESS,
            entries=entries,
            summary=summarize_entries(entries),
            metadata={
                "sequential": True,
                "checkpointed": True,
                "output_format": self._configuration.output_format,
                "configuration_sha256": self._configuration_sha256(),
            },
        )

    def _validate_manifest_source(
        self,
        manifest: ProductionImageAcquisitionManifest,
        source: ReadProductionVisualAssetPlan,
        assets: tuple[ProductionVisualAssetSpec, ...],
    ) -> None:
        if (
            manifest.source_visual_asset_plan_artifact_id != source.artifact_id
            or manifest.source_visual_asset_plan_sha256 != source.sha256
            or manifest.source_visual_asset_plan_schema_version != source.schema_version
            or tuple(entry.visual_asset_id for entry in manifest.entries)
            != tuple(asset.asset_id for asset in assets)
            or manifest.metadata.get("configuration_sha256") != self._configuration_sha256()
        ):
            raise ImageAcquisitionValidationError(
                "manifest source differs from durable visual asset plan"
            )

    @staticmethod
    def _validate_supported(
        assets: tuple[ProductionVisualAssetSpec, ...],
    ) -> None:
        unsupported = tuple(
            asset.asset_id
            for asset in assets
            if asset.asset_kind is not AssetKind.STILL_IMAGE
            or asset.generation_mode is not GenerationMode.TEXT_TO_IMAGE
        )
        if unsupported:
            raise ImageAcquisitionUnsupportedAssetException(
                "visual asset plan contains unsupported required assets"
            )

    def _provider_request(
        self,
        command: StageCommand,
        context: StageContext,
        asset: ProductionVisualAssetSpec,
    ) -> ImageAcquisitionProviderRequest:
        return ImageAcquisitionProviderRequest(
            job_id=command.job_id,
            command_id=command.command_id,
            correlation_id=context.correlation_id,
            attempt_number=command.attempt_number,
            visual_asset=asset,
            configuration=self._configuration,
        )

    @staticmethod
    def _stored_entry_from_binary(
        *,
        entry: ProductionImageAcquisitionEntry,
        binary: ProductionBinaryAsset,
        response: ImageAcquisitionProviderResponse | None,
        recovered: bool,
    ) -> ProductionImageAcquisitionEntry:
        return entry.model_copy(
            update={
                "status": ImageAcquisitionEntryStatus.STORED,
                "binary_asset_id": binary.asset_id,
                "binary_artifact_id": _binary_artifact_id(
                    binary.job_id,
                    entry.visual_asset_id,
                ),
                "storage_path": binary.storage_path,
                "mime_type": binary.mime_type,
                "extension": binary.extension,
                "sha256": binary.sha256,
                "size_bytes": binary.size_bytes,
                "width": binary.width,
                "height": binary.height,
                "provider": response.provider
                if response is not None
                else binary.metadata.provider or "orion-recovery",
                "requested_model": response.requested_model if response is not None else None,
                "reported_model": response.reported_model
                if response is not None
                else binary.metadata.model_version,
                "provider_request_id": response.request_id if response is not None else None,
                "input_tokens": response.input_tokens if response is not None else None,
                "output_tokens": response.output_tokens if response is not None else None,
                "total_tokens": response.total_tokens if response is not None else None,
                "cost_usd": response.cost_usd if response is not None else None,
                "http_status": response.http_status if response is not None else None,
                "latency_ms": response.latency_ms if response is not None else 0,
                "finish_reason": response.finish_reason if response is not None else "recovered",
                "error_code": None,
                "request_status": (
                    OpenRouterImageRequestStatus.COMPLETED
                    if response is not None and response.provider == "openrouter"
                    else entry.request_status
                ),
                "fresh_submission_permitted": (False if entry.request_status is not None else None),
                "metadata": {
                    "recovered": recovered,
                    "simulated": binary.metadata.attributes.get(
                        "simulated",
                        False,
                    ),
                },
            }
        )

    def _success(
        self,
        *,
        command: StageCommand,
        context: StageContext,
        source: ReadProductionVisualAssetPlan,
        manifest: ProductionImageAcquisitionManifest,
        stored_assets: dict[str, ProductionBinaryAsset],
        started_at: datetime,
    ) -> StageExecutionOutput:
        artifacts: list[Artifact] = []
        for entry in manifest.entries:
            binary = stored_assets[entry.visual_asset_id]
            artifact_id = _binary_artifact_id(
                command.job_id,
                entry.visual_asset_id,
            )
            artifacts.append(
                Artifact(
                    artifact_id=artifact_id,
                    job_id=command.job_id,
                    artifact_type=ArtifactType.SOURCE_IMAGE,
                    relative_path=binary.storage_path,
                    mime_type=binary.mime_type,
                    status=ArtifactStatus.READY,
                    size_bytes=binary.size_bytes,
                    sha256=binary.sha256,
                    width=binary.width,
                    height=binary.height,
                    provider=entry.provider,
                    model_version=entry.reported_model or entry.requested_model,
                    metadata={
                        "source_visual_asset_plan_artifact_id": str(source.artifact_id),
                        "source_visual_asset_plan_sha256": source.sha256,
                        "source_visual_asset_id": entry.visual_asset_id,
                        "source_scene_id": entry.source_scene_id,
                        "source_shot_id": entry.source_shot_id,
                        "role": entry.role.value,
                        "generation_mode": entry.generation_mode.value,
                        "width": binary.width,
                        "height": binary.height,
                        "prompt_version": binary.metadata.attributes.get("prompt_version"),
                        "prompt_sha256": binary.metadata.attributes.get("prompt_sha256"),
                        "provider": entry.provider,
                        "requested_model": entry.requested_model,
                        "reported_model": entry.reported_model,
                        "provider_request_id": entry.provider_request_id,
                        "input_tokens": entry.input_tokens,
                        "output_tokens": entry.output_tokens,
                        "total_tokens": entry.total_tokens,
                        "cost_usd": str(entry.cost_usd) if entry.cost_usd is not None else None,
                        "latency_ms": entry.latency_ms,
                        "finish_reason": entry.finish_reason,
                        "simulated": entry.metadata.get("simulated", False),
                        "deterministic": binary.metadata.deterministic,
                        "recovered": entry.metadata.get("recovered", False),
                    },
                )
            )
        manifest_content = serialize_image_acquisition_manifest(manifest)
        manifest_id = _manifest_artifact_id(
            command.job_id,
            command.attempt_number,
        )
        artifacts.append(
            Artifact(
                artifact_id=manifest_id,
                job_id=command.job_id,
                artifact_type=ArtifactType.PRODUCTION_IMAGE_ACQUISITION_MANIFEST,
                relative_path=image_acquisition_manifest_relative_path(context),
                mime_type="application/json",
                status=ArtifactStatus.READY,
                size_bytes=len(manifest_content),
                sha256=hashlib.sha256(manifest_content).hexdigest(),
                provider=self._provider_name,
                model_version=self._requested_model,
                metadata={
                    "schema_version": manifest.schema_version,
                    "source_visual_asset_plan_artifact_id": str(source.artifact_id),
                    "source_visual_asset_plan_sha256": source.sha256,
                    "entry_count": len(manifest.entries),
                    "stored_count": manifest.summary.stored,
                    "provider": manifest.provider,
                    "requested_model": manifest.requested_model,
                    "reported_models": list(manifest.reported_models),
                    "checkpointed": True,
                },
            )
        )
        finished_at = self._aware_now()
        result = StageResult(
            command_id=command.command_id,
            job_id=command.job_id,
            stage=command.stage,
            outcome=StageOutcome.SUCCEEDED,
            started_at=started_at,
            finished_at=finished_at,
            progress_percent=100,
            output_artifact_ids=tuple(item.artifact_id for item in artifacts),
            metadata={
                "handler": type(self).__name__,
                "provider": self._provider_name,
                "requested_model": self._requested_model,
                "source_visual_asset_plan_artifact_id": str(source.artifact_id),
                "image_count": len(stored_assets),
                "checkpointed": True,
            },
        )
        logger.info(
            "image acquisition stage completed",
            extra={
                "job_id": str(command.job_id),
                "command_id": str(command.command_id),
                "attempt": command.attempt_number,
                "provider": self._provider_name,
                "requested_model": self._requested_model,
                "image_count": len(stored_assets),
            },
        )
        return StageExecutionOutput(result=result, artifacts=tuple(artifacts))

    def _failure(
        self,
        command: StageCommand,
        started_at: datetime,
        outcome: StageOutcome,
        error_code: str,
        *,
        retry_after_seconds: float | None = None,
        diagnostic_subtype: ImageDiagnosticSubtype | None = None,
    ) -> StageExecutionOutput:
        logger.warning(
            "image acquisition stage did not complete",
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
                error_message="Image acquisition stage could not complete",
                retry_after_seconds=retry_after_seconds,
                metadata={
                    "handler": type(self).__name__,
                    "error_category": error_code,
                    "diagnostic_subtype": (
                        diagnostic_subtype.value if diagnostic_subtype is not None else None
                    ),
                },
            )
        )

    @staticmethod
    def _error_code(error: Exception) -> str:
        mapping: tuple[tuple[type[Exception], str], ...] = (
            (
                ProductionVisualAssetPlanTransientReadException,
                "visual_asset_plan_read_transient",
            ),
            (
                ProductionVisualAssetPlanReadError,
                "visual_asset_plan_invalid",
            ),
            (
                ImageAcquisitionUnsupportedAssetException,
                "image_asset_unsupported",
            ),
            (
                ImageAcquisitionProviderUncertainException,
                "image_provider_uncertain",
            ),
            (
                ImageAcquisitionProviderTimeoutException,
                "image_provider_timeout",
            ),
            (
                ImageAcquisitionProviderRateLimitException,
                "image_provider_rate_limit",
            ),
            (
                ImageAcquisitionProviderUnavailableException,
                "image_provider_unavailable",
            ),
            (
                ImageAcquisitionProviderAuthenticationException,
                "image_provider_authentication",
            ),
            (
                ImageAcquisitionProviderPolicyException,
                "image_provider_content_policy",
            ),
            (
                ImageAcquisitionProviderModelException,
                "image_provider_model",
            ),
            (
                ImageAcquisitionProviderResponseException,
                "image_provider_response",
            ),
            (
                ImageAcquisitionProviderContractException,
                "image_provider_contract",
            ),
            (
                ImageAcquisitionProviderConfigurationException,
                "image_provider_configuration",
            ),
            (ValidationError, "image_response_model_validation"),
            (TypeError, "image_type_error"),
            (ValueError, "image_validation_error"),
            (ImageAcquisitionProviderError, "image_provider_error"),
            (BinaryAssetError, "image_binary_integrity"),
            (ImageAcquisitionManifestError, "image_manifest_invalid"),
        )
        return next(
            (code for error_type, code in mapping if isinstance(error, error_type)),
            "image_acquisition_stage_error",
        )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("image acquisition clock must be timezone-aware")
        return value

    @staticmethod
    def _log_stored(
        command: StageCommand,
        entry: ProductionImageAcquisitionEntry,
        binary: ProductionBinaryAsset,
    ) -> None:
        logger.info(
            "image acquisition asset stored",
            extra={
                "job_id": str(command.job_id),
                "command_id": str(command.command_id),
                "attempt": command.attempt_number,
                "visual_asset_id": entry.visual_asset_id,
                "provider": entry.provider,
                "requested_model": entry.requested_model,
                "reported_model": entry.reported_model,
                "request_id": entry.provider_request_id,
                "latency_ms": entry.latency_ms,
                "output_mime": binary.mime_type,
                "width": binary.width,
                "height": binary.height,
                "size_bytes": binary.size_bytes,
                "cost_usd": (str(entry.cost_usd) if entry.cost_usd is not None else None),
                "recovered": entry.metadata.get("recovered", False),
                "simulated": entry.metadata.get("simulated", False),
            },
        )


def _entry_for(
    manifest: ProductionImageAcquisitionManifest,
    visual_asset_id: str,
) -> ProductionImageAcquisitionEntry:
    return next(entry for entry in manifest.entries if entry.visual_asset_id == visual_asset_id)


def _response_diagnostic(
    response: ImageAcquisitionProviderResponse,
) -> ImageDiagnosticMetadata | None:
    if not response.images:
        return None
    raw = response.images[0].provider_metadata.get("diagnostic")
    if isinstance(raw, dict):
        try:
            return ImageDiagnosticMetadata.model_validate(raw)
        except ValidationError:
            return None
    payload = response.images[0]
    width = payload.provider_metadata.get("width")
    height = payload.provider_metadata.get("height")
    return ImageDiagnosticMetadata(
        declared_media_type=payload.mime_type,
        detected_media_type=payload.mime_type,
        decoded_width=width if isinstance(width, int) and not isinstance(width, bool) else None,
        decoded_height=(
            height if isinstance(height, int) and not isinstance(height, bool) else None
        ),
        actual_aspect_ratio=(
            width / height
            if isinstance(width, int)
            and not isinstance(width, bool)
            and isinstance(height, int)
            and not isinstance(height, bool)
            and width > 0
            and height > 0
            else None
        ),
    )


def _diagnostic_subtype(error: Exception | None) -> ImageDiagnosticSubtype:
    explicit = getattr(error, "diagnostic_subtype", None)
    if isinstance(explicit, ImageDiagnosticSubtype):
        return explicit
    if isinstance(error, ImageAcquisitionProviderAuthenticationException):
        return ImageDiagnosticSubtype.PROVIDER_AUTHENTICATION
    if isinstance(error, ImageAcquisitionProviderRateLimitException):
        return ImageDiagnosticSubtype.PROVIDER_RATE_LIMIT
    if isinstance(error, ImageAcquisitionProviderUnavailableException):
        return ImageDiagnosticSubtype.PROVIDER_UNAVAILABLE
    if isinstance(error, ImageAcquisitionProviderPolicyException):
        return ImageDiagnosticSubtype.PROVIDER_POLICY
    if isinstance(error, ImageAcquisitionProviderModelException):
        return ImageDiagnosticSubtype.PROVIDER_MODEL
    if isinstance(error, ImageAcquisitionProviderContractException):
        return ImageDiagnosticSubtype.PROVIDER_CONTRACT
    if isinstance(
        error,
        (ImageAcquisitionProviderUncertainException, ImageAcquisitionProviderTimeoutException),
    ):
        return ImageDiagnosticSubtype.UNCERTAIN_TRANSPORT
    if isinstance(error, ImageAcquisitionProviderResponseException):
        return ImageDiagnosticSubtype.PROVIDER_ENVELOPE
    if isinstance(error, BinaryAssetError):
        return _binary_diagnostic_subtype(error)
    if isinstance(error, ImageAcquisitionManifestError):
        return ImageDiagnosticSubtype.MANIFEST_WRITE
    if isinstance(error, ValidationError):
        return ImageDiagnosticSubtype.RESPONSE_MODEL_VALIDATION
    return ImageDiagnosticSubtype.UNKNOWN_IMAGE_ERROR


def _binary_diagnostic_subtype(error: BinaryAssetError) -> ImageDiagnosticSubtype:
    if isinstance(error, BinaryAssetIOError):
        return ImageDiagnosticSubtype.BINARY_ASSET_WRITE
    if isinstance(
        error,
        (
            BinaryAssetConfigurationError,
            BinaryAssetConflictError,
            BinaryAssetCorruptError,
            BinaryAssetHashError,
            BinaryAssetLinkError,
            BinaryAssetMetadataError,
            BinaryAssetMimeError,
            BinaryAssetPathError,
            BinaryAssetSizeError,
        ),
    ):
        return ImageDiagnosticSubtype.BINARY_ASSET_VALIDATION
    return ImageDiagnosticSubtype.BINARY_ASSET_WRITE


def _validation_summary(
    error: Exception | None,
) -> tuple[str | None, str | None, str | None]:
    if error is None:
        return None, None, None
    if isinstance(error, ValidationError):
        first = error.errors(include_url=False, include_context=False, include_input=False)[0]
        code = _safe_error_code(str(first.get("type", "validation_error")))
        path = ".".join(str(part) for part in first.get("loc", ()))[:300] or "local"
        message = _bounded_error_message(str(first.get("msg", "local validation failed")))
        return code, path, message
    subtype = _diagnostic_subtype(error)
    return (
        subtype.value,
        _diagnostic_path(subtype),
        _diagnostic_message(error, subtype),
    )


def _diagnostic_path(subtype: ImageDiagnosticSubtype) -> str:
    if subtype in {
        ImageDiagnosticSubtype.BINARY_ASSET_VALIDATION,
        ImageDiagnosticSubtype.BINARY_ASSET_WRITE,
    }:
        return "binary_asset"
    if subtype is ImageDiagnosticSubtype.MANIFEST_WRITE:
        return "image_acquisition_manifest"
    if subtype is ImageDiagnosticSubtype.UNCERTAIN_TRANSPORT:
        return "transport"
    return "provider_response"


def _diagnostic_message(
    error: Exception,
    subtype: ImageDiagnosticSubtype,
) -> str:
    if isinstance(error, ImageAcquisitionProviderError):
        return _bounded_error_message(str(error))
    messages = {
        ImageDiagnosticSubtype.BINARY_ASSET_VALIDATION: ("binary asset validation failed"),
        ImageDiagnosticSubtype.BINARY_ASSET_WRITE: "binary asset write failed",
        ImageDiagnosticSubtype.MANIFEST_WRITE: "manifest checkpoint failed",
        ImageDiagnosticSubtype.UNCERTAIN_TRANSPORT: ("image submission outcome is uncertain"),
    }
    return messages.get(subtype, "image operation failed")


def _safe_error_code(value: str) -> str:
    normalized = "".join(
        character if character.isascii() and character.isalnum() else "_"
        for character in value.lower()
    )
    return "_".join(filter(None, normalized.split("_")))[:100] or "validation_error"


def _bounded_error_message(value: str) -> str:
    normalized = " ".join(value.split())
    return normalized[:500] or "image operation failed"


def _durable_reported_cost(
    candidate: object,
    fallback: Decimal | None,
) -> Decimal | None:
    value = candidate if isinstance(candidate, Decimal) else fallback
    if value is None or not value.is_finite() or value < 0:
        return fallback
    exponent = value.as_tuple().exponent
    digits = len(value.as_tuple().digits)
    if not isinstance(exponent, int) or exponent < -9 or digits > 18:
        return fallback
    return value


def _binary_asset_id(visual_asset_id: str) -> str:
    return f"image-{visual_asset_id}"


def _safe_image_dimension(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ImageAcquisitionProviderContractException(
            "image provider omitted validated image dimensions"
        )
    return value


def _binary_artifact_id(job_id: UUID, visual_asset_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"orion:{job_id}:source-image:{visual_asset_id}")


def _manifest_artifact_id(job_id: UUID, attempt_number: int) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"orion:{job_id}:image-acquisition-manifest:{attempt_number}",
    )
