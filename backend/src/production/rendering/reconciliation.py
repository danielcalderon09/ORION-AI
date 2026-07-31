"""Read-only reconciliation of durable local render preparation."""

from __future__ import annotations

from backend.src.production.rendering.configuration import RenderingConfiguration
from backend.src.production.rendering.exceptions import (
    RenderingCorruptError,
    RenderingError,
    RenderingSourceError,
)
from backend.src.production.rendering.models import (
    RendererKind,
    RendererReadiness,
    RenderManifestStatus,
    RenderReconciliationResult,
)
from backend.src.production.rendering.ports import (
    LocalRenderer,
    LocalRenderStore,
    RenderArtifactInventory,
    RenderCompositionSourceReader,
    RenderStageContext,
)
from backend.src.production.rendering.recovery import validate_manifest_identity
from backend.src.production.rendering.renderers import (
    DryRunRenderer,
    renderer_descriptions,
)
from backend.src.production.rendering.request_builder import (
    build_local_render_request,
    render_request_fingerprint,
)


class LocalRenderReconciler:
    def __init__(
        self,
        *,
        source_reader: RenderCompositionSourceReader,
        store: LocalRenderStore,
        configuration: RenderingConfiguration,
        renderer: LocalRenderer | None = None,
        artifact_inventory: RenderArtifactInventory | None = None,
    ) -> None:
        self._source_reader = source_reader
        self._store = store
        self._configuration = configuration
        self._renderer = renderer or DryRunRenderer()
        self._artifact_inventory = artifact_inventory

    async def reconcile(
        self,
        *,
        context: RenderStageContext,
    ) -> RenderReconciliationResult:
        issues: list[str] = []
        source_plan_present = False
        source_manifest_present = False
        source = None
        expected = None
        try:
            source = await self._source_reader.read(context=context)
            source_plan_present = True
            source_manifest_present = True
            expected = build_local_render_request(source, self._configuration)
        except RenderingSourceError as exc:
            issues.append(exc.code)
            if exc.code == "source_plan_missing":
                source_manifest_present = True
            elif exc.code == "source_manifest_missing":
                source_plan_present = True
            elif exc.code != "source_missing":
                source_plan_present = True
                source_manifest_present = True
        except RenderingError:
            issues.append("source_invalid")

        request = None
        manifest = None
        corrupt_state = bool(
            set(issues)
            & {
                "source_corrupt",
                "source_invalid",
                "source_oversized",
                "source_unsafe",
            }
        )
        try:
            request = await self._store.read_request(context=context)
        except RenderingCorruptError:
            corrupt_state = True
            issues.append("request_corrupt")
        try:
            manifest = await self._store.read_manifest(context=context)
        except RenderingCorruptError:
            corrupt_state = True
            issues.append("manifest_corrupt")
        execution_plan = None
        if self._configuration.renderer is RendererKind.FFMPEG:
            try:
                execution_plan = await self._store.read_execution_plan(context=context)
            except RenderingCorruptError:
                corrupt_state = True
                issues.append("execution_plan_corrupt")
            if execution_plan is None and "execution_plan_corrupt" not in issues:
                issues.append("execution_plan_missing")

        if request is None and "request_corrupt" not in issues:
            issues.append("request_missing")
        if manifest is None and "manifest_corrupt" not in issues:
            issues.append("manifest_missing")

        fingerprint_valid = bool(
            request and render_request_fingerprint(request) == request.request_fingerprint
        )
        if request is not None and not fingerprint_valid:
            corrupt_state = True
            issues.append("request_fingerprint_invalid")
        source_matches = bool(
            source
            and (
                request is None
                or (expected and expected.request_fingerprint == request.request_fingerprint)
            )
        )
        stale_source = bool(expected and request and not source_matches)
        if stale_source:
            issues.append("source_stale")

        if manifest is not None and request is not None:
            try:
                validate_manifest_identity(
                    manifest,
                    request=request,
                    capabilities=self._renderer.capabilities,
                )
            except RenderingError:
                stale_source = True
                issues.append("manifest_identity_mismatch")

        output_path = (
            request.requested_output.relative_path
            if request is not None
            else (expected.requested_output.relative_path if expected is not None else None)
        )
        unexpected_output = False
        if output_path is not None:
            try:
                unexpected_output = await self._store.output_exists(relative_path=output_path)
            except RenderingError:
                corrupt_state = True
                issues.append("output_path_unsafe")
        dry_result_present = bool(manifest and manifest.dry_run_result)
        dry_accepted = bool(
            manifest and manifest.dry_run_result and manifest.dry_run_result.accepted
        )
        media_produced = bool(manifest and manifest.media_produced)
        if media_produced and self._configuration.renderer is RendererKind.DRY_RUN:
            corrupt_state = True
            issues.append("false_media_produced_state")
        output_agrees = False
        if unexpected_output and manifest and manifest.media_produced:
            try:
                output_agrees = await self._store.output_identity(
                    relative_path=manifest.output_relative_path
                ) == (manifest.output_size_bytes, manifest.output_sha256)
            except RenderingError:
                corrupt_state = True
                issues.append("output_identity_invalid")
            if not output_agrees:
                issues.append("output_checksum_mismatch")
        elif unexpected_output:
            issues.append("unexpected_output_file")
        terminal_invalid = bool(
            manifest
            and manifest.status in {RenderManifestStatus.INVALID, RenderManifestStatus.FAILED}
        )
        temporary_output = False
        if execution_plan is not None:
            try:
                temporary_output = await self._store.output_exists(
                    relative_path=execution_plan.temporary_output_relative_path
                )
            except RenderingError:
                corrupt_state = True
                issues.append("temporary_output_unsafe")
        interrupted = bool(
            manifest
            and manifest.status in {RenderManifestStatus.RENDERING, RenderManifestStatus.PROBING}
        )
        if interrupted:
            issues.append("render_interrupted")
        failure_code = str(manifest.metadata.get("failure_code", "")) if manifest else ""
        output_artifact_present = False
        output_artifact_agrees = False
        if self._artifact_inventory is not None and manifest is not None:
            from backend.src.production.domain.enums import ArtifactStatus, ArtifactType

            artifacts = await self._artifact_inventory.list_for_job(context.job_id)
            matches = tuple(
                item
                for item in artifacts
                if item.artifact_type is ArtifactType.LONG_FORM_RENDER
                and item.status is ArtifactStatus.READY
                and item.artifact_id == manifest.output_artifact_id
            )
            output_artifact_present = len(matches) == 1
            if output_artifact_present:
                item = matches[0]
                output_artifact_agrees = (
                    item.relative_path == manifest.output_relative_path
                    and item.sha256 == manifest.output_sha256
                    and item.size_bytes == manifest.output_size_bytes
                    and item.metadata.get("request_fingerprint") == manifest.request_fingerprint
                )
                if not output_artifact_agrees:
                    issues.append("output_artifact_mismatch")
        complete = bool(
            source
            and request
            and manifest
            and fingerprint_valid
            and source_matches
            and manifest.status is RenderManifestStatus.VALIDATED
            and (
                (
                    request.renderer_kind is RendererKind.DRY_RUN
                    and dry_accepted
                    and not media_produced
                )
                or (
                    request.renderer_kind is RendererKind.FFMPEG
                    and manifest.ffmpeg_result is not None
                    and media_produced
                    and output_agrees
                    and execution_plan is not None
                    and (self._artifact_inventory is None or output_artifact_agrees)
                )
            )
            and not corrupt_state
        )
        manual = bool(
            source is None
            or corrupt_state
            or stale_source
            or (unexpected_output and not output_agrees)
            or terminal_invalid
        )
        readiness = next(
            item.readiness
            for item in renderer_descriptions(self._configuration.renderer)
            if item.renderer_kind is self._configuration.renderer
        )
        return RenderReconciliationResult(
            job_id=context.job_id,
            attempt_number=context.attempt_number,
            source_plan_present=source_plan_present,
            source_manifest_present=source_manifest_present,
            request_present=request is not None,
            execution_manifest_present=manifest is not None,
            schemas_supported=not corrupt_state,
            source_identities_match=source_matches,
            request_fingerprint_valid=fingerprint_valid,
            renderer_kind=self._configuration.renderer,
            renderer_readiness=readiness or RendererReadiness.NOT_CONFIGURED,
            execution_plan_present=execution_plan is not None,
            execution_plan_valid=execution_plan is not None and not corrupt_state,
            normalized_ffmpeg_version=(
                manifest.ffmpeg_result.renderer_version
                if manifest and manifest.ffmpeg_result
                else None
            ),
            normalized_ffprobe_version=(
                manifest.ffmpeg_result.ffprobe_version
                if manifest and manifest.ffmpeg_result
                else None
            ),
            dry_run_result_present=dry_result_present,
            dry_run_accepted=dry_accepted,
            media_produced=media_produced,
            temporary_output_present=temporary_output,
            final_output_present=unexpected_output,
            final_output_checksum_agrees=output_agrees,
            output_artifact_present=output_artifact_present,
            output_artifact_metadata_agrees=output_artifact_agrees,
            probe_validation_complete=bool(
                manifest and manifest.ffmpeg_result and manifest.ffmpeg_result.probe_fingerprint
            ),
            interrupted_render=interrupted,
            failed_render=terminal_invalid,
            timed_out=failure_code == "ffmpeg_timeout",
            unexpected_output_file=unexpected_output,
            stale_source=stale_source,
            corrupt_state=corrupt_state,
            recovery_safe=bool(source and expected and not manual and not complete),
            manual_intervention_required=manual,
            stage_complete=complete,
            issues=tuple(issues),
        )
