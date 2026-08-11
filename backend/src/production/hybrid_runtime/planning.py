"""Opt-in post-TTS hybrid strategy and aggregate budget application boundary."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome, StageResult
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.enums import ArtifactStatus, ArtifactType, ProductionStage
from backend.src.production.planning.aggregate_visual_budget import (
    AggregateVisualBudgetError,
    AggregateVisualBudgetPlan,
    HybridVisualBudgetAuthorization,
    build_aggregate_visual_budget_plan,
    deserialize_aggregate_visual_budget_plan,
    serialize_aggregate_visual_budget_plan,
)
from backend.src.production.planning.visual_strategy import (
    HybridVisualStrategyPlan,
    VisualStrategyName,
    build_hybrid_visual_strategy_plan,
    deserialize_hybrid_visual_strategy_plan,
    serialize_hybrid_visual_strategy_plan,
)
from backend.src.production.runtime.context import StageContext
from backend.src.production.runtime.handlers.base import StageHandler
from backend.src.production.runtime.runtime_models import StageExecutionOutput
from backend.src.production.visual_asset_planning.models import ProductionVisualAssetPlan
from backend.src.production.visual_asset_planning.shot_expansion import PostTtsShotExpansion

STRATEGY_FILENAME = "hybrid-visual-strategy-plan.json"
BUDGET_FILENAME = "aggregate-visual-budget-plan.json"


class HybridRuntimeDriftError(ValueError):
    """A durable hybrid decision differs from the proposed recovery decision."""


class LocalHybridPlanningStore:
    """Write immutable hybrid planning sidecars beside final visual planning."""

    def __init__(self, workspace_root: Path) -> None:
        self._root = workspace_root.resolve()

    def read_sources(
        self, output: StageExecutionOutput
    ) -> tuple[PostTtsShotExpansion, ProductionVisualAssetPlan, Artifact, Artifact]:
        expansion_artifact = next(
            (
                item
                for item in output.artifacts
                if item.artifact_type is ArtifactType.PRODUCTION_SHOT_EXPANSION
            ),
            None,
        )
        visual_artifact = next(
            (
                item
                for item in output.artifacts
                if item.artifact_type is ArtifactType.PRODUCTION_VISUAL_ASSET_PLAN
            ),
            None,
        )
        if expansion_artifact is None or visual_artifact is None:
            raise HybridRuntimeDriftError(
                "hybrid strategy requires durable post-TTS shot expansion and visual plan"
            )
        expansion_content = self._read_verified(expansion_artifact)
        visual_content = self._read_verified(visual_artifact)
        return (
            PostTtsShotExpansion.model_validate_json(expansion_content),
            ProductionVisualAssetPlan.model_validate_json(visual_content),
            expansion_artifact,
            visual_artifact,
        )

    def reconcile(
        self,
        *,
        context: StageContext,
        strategy: HybridVisualStrategyPlan,
        budget: AggregateVisualBudgetPlan,
    ) -> tuple[tuple[str, bytes], tuple[str, bytes]]:
        directory = self._resolve(context.workspace_relative_path)
        strategy_path = directory / STRATEGY_FILENAME
        budget_path = directory / BUDGET_FILENAME
        strategy_content = serialize_hybrid_visual_strategy_plan(strategy)
        budget_content = serialize_aggregate_visual_budget_plan(budget)
        previous_strategy = self._latest_before(context, STRATEGY_FILENAME)
        previous_budget = self._latest_before(context, BUDGET_FILENAME)
        if previous_strategy is not None:
            recovered = deserialize_hybrid_visual_strategy_plan(previous_strategy.read_bytes())
            if recovered != strategy:
                raise HybridRuntimeDriftError("hybrid visual strategy drifted during recovery")
        if previous_budget is not None:
            recovered_budget = deserialize_aggregate_visual_budget_plan(previous_budget.read_bytes())
            if recovered_budget != budget:
                raise HybridRuntimeDriftError("aggregate visual budget drifted during recovery")
        self._write_immutable(strategy_path, strategy_content)
        self._write_immutable(budget_path, budget_content)
        return (
            (self._relative(strategy_path), strategy_content),
            (self._relative(budget_path), budget_content),
        )

    def _read_verified(self, artifact: Artifact) -> bytes:
        target = self._resolve(artifact.relative_path)
        content = target.read_bytes()
        if artifact.size_bytes is not None and artifact.size_bytes != len(content):
            raise HybridRuntimeDriftError("hybrid planning source size differs")
        if artifact.sha256 is not None and artifact.sha256 != hashlib.sha256(content).hexdigest():
            raise HybridRuntimeDriftError("hybrid planning source checksum differs")
        return content

    def _latest_before(self, context: StageContext, filename: str) -> Path | None:
        job_root = self._resolve(f"production/{context.job_id}/visual_asset_planning")
        if not job_root.exists():
            return None
        candidates: list[tuple[int, Path]] = []
        for path in job_root.glob(f"attempt-*/{filename}"):
            try:
                attempt = int(path.parent.name.removeprefix("attempt-"))
            except ValueError:
                continue
            if attempt < context.attempt_number:
                candidates.append((attempt, path))
        return max(candidates, default=(0, None), key=lambda item: item[0])[1]

    def _write_immutable(self, target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != content:
                raise HybridRuntimeDriftError("hybrid planning artifact already differs")
            return
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _resolve(self, relative: str) -> Path:
        target = (self._root / Path(relative)).resolve()
        if target != self._root and self._root not in target.parents:
            raise HybridRuntimeDriftError("hybrid artifact escaped workspace")
        return target

    def _relative(self, target: Path) -> str:
        return target.relative_to(self._root).as_posix()


class HybridVisualPlanningHandler:
    """Decorate final visual planning with immutable hybrid decisions."""

    supported_stages = frozenset({ProductionStage.VISUAL_ASSET_PLANNING})

    def __init__(
        self,
        *,
        delegate: StageHandler,
        strategy_name: VisualStrategyName,
        authorization: HybridVisualBudgetAuthorization,
        store: LocalHybridPlanningStore,
        clock: Callable[[], datetime],
        uuid_factory: Callable[[], UUID],
    ) -> None:
        if strategy_name is VisualStrategyName.FULL_VIDEO:
            raise ValueError("full-video runtime must keep the legacy planning handler")
        self._delegate = delegate
        self._strategy_name = strategy_name
        self._authorization = authorization
        self._store = store
        self._clock = clock
        self._uuid_factory = uuid_factory

    async def execute(
        self, command: StageCommand, context: StageContext
    ) -> StageExecutionOutput:
        output = await self._delegate.execute(command, context)
        if output.result.outcome is not StageOutcome.SUCCEEDED:
            return output
        started = self._aware_now()
        failure_artifacts: tuple[Artifact, ...] = ()
        try:
            expansion, _, expansion_artifact, _ = self._store.read_sources(output)
            strategy = build_hybrid_visual_strategy_plan(
                job_id=command.job_id,
                source_shot_expansion_artifact_id=expansion_artifact.artifact_id,
                source_shot_expansion_sha256=expansion_artifact.sha256 or "",
                source_shot_expansion_fingerprint=expansion.plan_fingerprint,
                shots=expansion.allocations,
                strategy_name=self._strategy_name,
            )
            budget = build_aggregate_visual_budget_plan(
                strategy_plan=strategy,
                authorization=self._authorization,
            )
            strategy_written, budget_written = self._store.reconcile(
                context=context,
                strategy=strategy,
                budget=budget,
            )
            strategy_artifact = self._artifact(
                command,
                ArtifactType.HYBRID_VISUAL_STRATEGY_PLAN,
                strategy_written,
                strategy.fingerprint,
            )
            budget_artifact = self._artifact(
                command,
                ArtifactType.AGGREGATE_VISUAL_BUDGET_PLAN,
                budget_written,
                budget.fingerprint,
            )
            artifacts = (*output.artifacts, strategy_artifact, budget_artifact)
            failure_artifacts = artifacts
            metadata = {
                **output.result.metadata,
                "visual_strategy": strategy.strategy_name.value,
                "visual_shots": strategy.summary.visual_shot_count,
                "generated_video_shots": strategy.summary.generated_video_shots,
                "generated_image_shots": strategy.summary.generated_image_shots,
                "reused_video_shots": strategy.summary.reused_video_shots,
                "reused_image_shots": strategy.summary.reused_image_shots,
                "image_requests": budget.image_requests,
                "video_requests": budget.video_requests,
                "purchased_video_seconds": budget.purchased_video_seconds,
                "estimated_visual_cost_usd": str(budget.estimated_total_visual_cost_usd),
                "hybrid_strategy_fingerprint": strategy.fingerprint,
                "aggregate_budget_fingerprint": budget.fingerprint,
            }
            if not budget.budget_pass:
                raise AggregateVisualBudgetError(budget)
            result = output.result.model_copy(
                update={
                    "output_artifact_ids": tuple(item.artifact_id for item in artifacts),
                    "metadata": metadata,
                }
            )
            return StageExecutionOutput(result=result, artifacts=artifacts)
        except (AggregateVisualBudgetError, HybridRuntimeDriftError, ValueError) as exc:
            finished = self._aware_now()
            return StageExecutionOutput(
                result=StageResult(
                    command_id=command.command_id,
                    job_id=command.job_id,
                    stage=command.stage,
                    outcome=StageOutcome.FAILED_PERMANENT,
                    started_at=started,
                    finished_at=finished,
                    progress_percent=100,
                    output_artifact_ids=tuple(
                        item.artifact_id for item in failure_artifacts
                    ),
                    error_code=(
                        "aggregate_visual_budget_rejected"
                        if isinstance(exc, AggregateVisualBudgetError)
                        else "hybrid_visual_planning_drift"
                    ),
                    error_message=str(exc),
                    metadata={"handler": type(self).__name__},
                ),
                artifacts=failure_artifacts,
            )

    def _artifact(
        self,
        command: StageCommand,
        artifact_type: ArtifactType,
        written: tuple[str, bytes],
        fingerprint: str,
    ) -> Artifact:
        relative_path, content = written
        return Artifact(
            artifact_id=self._uuid_factory(),
            job_id=command.job_id,
            artifact_type=artifact_type,
            relative_path=relative_path,
            mime_type="application/json",
            status=ArtifactStatus.READY,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            provider="orion-local",
            model_version="hybrid-visual-runtime-v1",
            metadata={"fingerprint": fingerprint},
        )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("hybrid runtime clock must be timezone-aware")
        return value


def hybrid_budget_authorization_from_values(
    *,
    image_cost: Decimal,
    video_price_per_second: Decimal,
    maximum_image_requests: int,
    maximum_video_requests: int,
    maximum_image_cost: Decimal,
    maximum_video_cost_per_request: Decimal,
    maximum_video_cost: Decimal,
    maximum_total_visual_cost: Decimal,
) -> HybridVisualBudgetAuthorization:
    return HybridVisualBudgetAuthorization(
        estimated_image_cost_per_request_usd=image_cost,
        video_price_per_second_usd=video_price_per_second,
        maximum_image_requests=maximum_image_requests,
        maximum_video_requests=maximum_video_requests,
        maximum_authorized_image_cost_usd=maximum_image_cost,
        maximum_authorized_video_cost_per_request_usd=maximum_video_cost_per_request,
        maximum_authorized_video_cost_usd=maximum_video_cost,
        maximum_authorized_total_visual_cost_usd=maximum_total_visual_cost,
    )
