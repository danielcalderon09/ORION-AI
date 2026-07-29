"""Read-only reconciliation of a composition plan and its source assets."""

from __future__ import annotations

from backend.src.production.media_composition.configuration import (
    MediaCompositionConfiguration,
)
from backend.src.production.media_composition.domain.models import (
    CompositionAssetAvailability,
    CompositionIssueSeverity,
    CompositionValidationIssue,
    MediaCompositionReconciliationResult,
)
from backend.src.production.media_composition.domain.timeline import (
    build_media_composition_plan,
)
from backend.src.production.media_composition.exceptions import (
    MediaCompositionError,
)
from backend.src.production.media_composition.ports import (
    MediaCompositionSourceReader,
    MediaCompositionStageContext,
    MediaCompositionStore,
)


class MediaCompositionReconciler:
    def __init__(
        self,
        *,
        source_reader: MediaCompositionSourceReader,
        store: MediaCompositionStore,
        configuration: MediaCompositionConfiguration,
    ) -> None:
        self._source_reader = source_reader
        self._store = store
        self._configuration = configuration

    async def reconcile(
        self,
        *,
        context: MediaCompositionStageContext,
    ) -> MediaCompositionReconciliationResult:
        plan = None
        manifest = None
        issues: list[CompositionValidationIssue] = []
        try:
            plan = await self._store.read_plan(context=context)
        except MediaCompositionError:
            issues.append(
                CompositionValidationIssue(
                    code="plan_corrupt",
                    severity=CompositionIssueSeverity.ERROR,
                )
            )
        try:
            manifest = await self._store.read_manifest(context=context)
        except MediaCompositionError:
            issues.append(
                CompositionValidationIssue(
                    code="manifest_corrupt",
                    severity=CompositionIssueSeverity.ERROR,
                )
            )
        try:
            source = await self._source_reader.read(context=context)
            expected = build_media_composition_plan(source, self._configuration)
        except MediaCompositionError:
            issues.append(
                CompositionValidationIssue(
                    code="source_invalid",
                    severity=CompositionIssueSeverity.ERROR,
                )
            )
            source = None
            expected = None
        missing = (
            tuple(
                item.asset_id
                for item in source.asset_validation
                if item.availability is CompositionAssetAvailability.MISSING
            )
            if source is not None
            else ()
        )
        corrupt = (
            tuple(
                item.asset_id
                for item in source.asset_validation
                if item.availability is CompositionAssetAvailability.CORRUPT
            )
            if source is not None
            else ()
        )
        source_matches = (
            plan is not None
            and expected is not None
            and plan.plan_fingerprint == expected.plan_fingerprint
        )
        if plan is None:
            issues.append(
                CompositionValidationIssue(
                    code="plan_missing",
                    severity=CompositionIssueSeverity.ERROR,
                )
            )
        if manifest is None:
            issues.append(
                CompositionValidationIssue(
                    code="manifest_missing",
                    severity=CompositionIssueSeverity.ERROR,
                )
            )
        if plan is not None and expected is not None and not source_matches:
            issues.append(
                CompositionValidationIssue(
                    code="source_fingerprint_mismatch",
                    severity=CompositionIssueSeverity.ERROR,
                )
            )
        complete = bool(
            plan
            and manifest
            and source_matches
            and not missing
            and not corrupt
            and manifest.status.value == "complete"
        )
        return MediaCompositionReconciliationResult(
            job_id=context.job_id,
            attempt_number=context.attempt_number,
            plan_present=plan is not None,
            manifest_present=manifest is not None,
            plan_valid=plan is not None,
            manifest_valid=manifest is not None,
            source_matches=source_matches,
            expected_asset_count=len(source.assets) if source is not None else 0,
            available_asset_count=(
                sum(
                    item.availability is CompositionAssetAvailability.AVAILABLE
                    for item in source.asset_validation
                )
                if source is not None
                else 0
            ),
            missing_asset_ids=missing,
            corrupt_asset_ids=corrupt,
            orphan_asset_ids=source.orphan_asset_ids if source is not None else (),
            recovery_safe=bool(source_matches and not corrupt),
            manual_intervention_required=bool(corrupt or not source_matches),
            stage_complete=complete,
            issues=tuple(issues),
        )
