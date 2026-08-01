"""Read-only reconciliation of durable OpenRouter scripting request records."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from backend.src.production.domain.base import ContractModel
from backend.src.production.scripting.openrouter_request import (
    OpenRouterScriptingRequestStatus,
)
from backend.src.production.scripting.openrouter_request_store import (
    OpenRouterScriptingRequestStore,
    OpenRouterScriptingRequestStoreError,
)


class OpenRouterScriptingReconciliationReport(ContractModel):
    job_id: UUID
    request_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    uncertain_submission: bool
    submitting_interrupted: bool
    script_checkpoint_recoverable: bool
    source_plan_present: bool
    source_plan_changed: bool
    model_mismatch: bool
    usage_or_cost_inconsistent: bool
    script_checksum_matches: bool | None = None
    corrupt_state: bool
    automatic_submission_safe: bool
    manual_intervention_required: bool
    issues: tuple[str, ...] = ()


class OpenRouterScriptingRequestReconciler:
    def __init__(self, store: OpenRouterScriptingRequestStore) -> None:
        self._store = store

    async def reconcile(
        self,
        *,
        job_id: UUID,
        expected_source_plan_sha256: str | None = None,
        expected_model: str | None = None,
        source_plan_present: bool = True,
        script_artifact_present: bool | None = None,
        registered_script_sha256: str | None = None,
    ) -> OpenRouterScriptingReconciliationReport:
        try:
            records = await self._store.list_for_job(job_id=job_id)
        except OpenRouterScriptingRequestStoreError:
            return OpenRouterScriptingReconciliationReport(
                job_id=job_id,
                request_count=0,
                completed_count=0,
                uncertain_submission=False,
                submitting_interrupted=False,
                script_checkpoint_recoverable=False,
                source_plan_present=source_plan_present,
                source_plan_changed=False,
                model_mismatch=False,
                usage_or_cost_inconsistent=False,
                corrupt_state=True,
                automatic_submission_safe=False,
                manual_intervention_required=True,
                issues=("corrupt_request_state",),
            )
        issues: list[str] = []
        uncertain = any(
            item.status is OpenRouterScriptingRequestStatus.UNCERTAIN for item in records
        )
        submitting = any(
            item.status is OpenRouterScriptingRequestStatus.SUBMITTING for item in records
        )
        completed = tuple(
            item for item in records if item.status is OpenRouterScriptingRequestStatus.COMPLETED
        )
        source_changed = bool(
            expected_source_plan_sha256 is not None
            and any(
                item.fingerprint_input.source_plan_sha256 != expected_source_plan_sha256
                for item in records
            )
        )
        model_mismatch = bool(
            expected_model is not None
            and any(item.fingerprint_input.model != expected_model for item in records)
        )
        usage_or_cost_inconsistent = any(
            (
                item.total_tokens is not None
                and item.input_tokens is not None
                and item.output_tokens is not None
                and item.total_tokens != item.input_tokens + item.output_tokens
            )
            or (
                item.reported_cost_usd is not None
                and item.reported_cost_usd > item.maximum_authorized_cost_usd
            )
            for item in records
        )
        checksum_matches: bool | None = None
        if registered_script_sha256 is not None and completed:
            checksum_matches = any(
                item.script_sha256 == registered_script_sha256 for item in completed
            )
        if uncertain:
            issues.append("uncertain_submission")
        if submitting:
            issues.append("interrupted_submission")
        if source_changed:
            issues.append("source_plan_changed")
        if records and not source_plan_present:
            issues.append("orphan_request_record")
        if not records:
            issues.append("missing_request_record")
        if model_mismatch:
            issues.append("model_mismatch")
        if completed and script_artifact_present is False:
            issues.append("script_missing_after_completed_request")
        if checksum_matches is False:
            issues.append("script_checksum_mismatch")
        if usage_or_cost_inconsistent:
            issues.append("usage_or_cost_inconsistent")
        if any(item.metadata.get("raw_response_persisted") is not False for item in records):
            issues.append("unsafe_raw_response_marker")
        non_blocking = {"missing_request_record"}
        manual = any(issue not in non_blocking for issue in issues)
        return OpenRouterScriptingReconciliationReport(
            job_id=job_id,
            request_count=len(records),
            completed_count=len(completed),
            uncertain_submission=uncertain,
            submitting_interrupted=submitting,
            script_checkpoint_recoverable=bool(completed),
            source_plan_present=source_plan_present,
            source_plan_changed=source_changed,
            model_mismatch=model_mismatch,
            usage_or_cost_inconsistent=usage_or_cost_inconsistent,
            script_checksum_matches=checksum_matches,
            corrupt_state=False,
            automatic_submission_safe=not manual and not completed,
            manual_intervention_required=manual,
            issues=tuple(issues),
        )


__all__ = [
    "OpenRouterScriptingReconciliationReport",
    "OpenRouterScriptingRequestReconciler",
]
