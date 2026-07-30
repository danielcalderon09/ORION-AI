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
    LocalRenderStore,
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
    ) -> None:
        self._source_reader = source_reader
        self._store = store
        self._configuration = configuration

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

        renderer = DryRunRenderer()
        if manifest is not None and request is not None:
            try:
                validate_manifest_identity(
                    manifest,
                    request=request,
                    capabilities=renderer.capabilities,
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
        if unexpected_output:
            issues.append("unexpected_output_file")

        dry_result_present = bool(manifest and manifest.dry_run_result)
        dry_accepted = bool(
            manifest and manifest.dry_run_result and manifest.dry_run_result.accepted
        )
        media_produced = bool(manifest and manifest.media_produced)
        if media_produced:
            corrupt_state = True
            issues.append("false_media_produced_state")
        terminal_invalid = bool(
            manifest
            and manifest.status in {RenderManifestStatus.INVALID, RenderManifestStatus.FAILED}
        )
        complete = bool(
            source
            and request
            and manifest
            and fingerprint_valid
            and source_matches
            and manifest.status is RenderManifestStatus.VALIDATED
            and dry_accepted
            and not media_produced
            and not unexpected_output
            and not corrupt_state
        )
        manual = bool(
            source is None or corrupt_state or stale_source or unexpected_output or terminal_invalid
        )
        readiness = next(
            item.readiness
            for item in renderer_descriptions()
            if item.renderer_kind is RendererKind.DRY_RUN
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
            renderer_kind=RendererKind.DRY_RUN,
            renderer_readiness=readiness or RendererReadiness.NOT_CONFIGURED,
            dry_run_result_present=dry_result_present,
            dry_run_accepted=dry_accepted,
            media_produced=media_produced,
            unexpected_output_file=unexpected_output,
            stale_source=stale_source,
            corrupt_state=corrupt_state,
            recovery_safe=bool(source and expected and not manual and not complete),
            manual_intervention_required=manual,
            stage_complete=complete,
            issues=tuple(issues),
        )
