"""Small ports for final render validation."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from backend.src.production.domain.artifact import Artifact
from backend.src.production.media_composition.domain.models import (
    MediaCompositionManifest,
    MediaCompositionPlan,
)
from backend.src.production.render_validation.models import FinalRenderValidationManifest
from backend.src.production.rendering.models import (
    FFmpegExecutionPlan,
    LocalRenderRequest,
    RenderExecutionManifest,
)
from backend.src.production.rendering.output_probe import ProbedRenderOutput


class FinalValidationStageContext(Protocol):
    job_id: UUID
    attempt_number: int


class FinalValidationArtifactInventory(Protocol):
    async def list_for_job(self, job_id: UUID) -> tuple[Artifact, ...]: ...


class VerifiedFinalRenderSource:
    def __init__(
        self,
        *,
        render_artifact: Artifact,
        request_artifact: Artifact,
        execution_plan_artifact: Artifact,
        render_manifest_artifact: Artifact,
        composition_plan_artifact: Artifact,
        composition_manifest_artifact: Artifact,
        request: LocalRenderRequest,
        execution_plan: FFmpegExecutionPlan,
        render_manifest: RenderExecutionManifest,
        composition_plan: MediaCompositionPlan,
        composition_manifest: MediaCompositionManifest,
        render_path: Path,
    ) -> None:
        self.render_artifact = render_artifact
        self.request_artifact = request_artifact
        self.execution_plan_artifact = execution_plan_artifact
        self.render_manifest_artifact = render_manifest_artifact
        self.composition_plan_artifact = composition_plan_artifact
        self.composition_manifest_artifact = composition_manifest_artifact
        self.request = request
        self.execution_plan = execution_plan
        self.render_manifest = render_manifest
        self.composition_plan = composition_plan
        self.composition_manifest = composition_manifest
        self.render_path = render_path


class FinalRenderSourceReader(Protocol):
    async def read(
        self,
        *,
        context: FinalValidationStageContext,
        input_artifact_ids: tuple[UUID, ...],
    ) -> VerifiedFinalRenderSource: ...


class FinalRenderProbe(Protocol):
    async def probe(self, source: VerifiedFinalRenderSource) -> ProbedRenderOutput: ...


class FinalRenderValidationStore(Protocol):
    async def read_manifest(
        self,
        *,
        context: FinalValidationStageContext,
    ) -> FinalRenderValidationManifest | None: ...

    async def create_manifest(
        self,
        *,
        context: FinalValidationStageContext,
        manifest: FinalRenderValidationManifest,
    ) -> tuple[str, int, str]: ...

    async def checkpoint_manifest(
        self,
        *,
        context: FinalValidationStageContext,
        previous: FinalRenderValidationManifest,
        current: FinalRenderValidationManifest,
    ) -> tuple[str, int, str]: ...

    async def manifest_identity(
        self,
        *,
        context: FinalValidationStageContext,
    ) -> tuple[str, int, str]: ...

    async def media_identity(self, *, relative_path: str) -> tuple[int, str]: ...


class FinalValidationClock(Protocol):
    def __call__(self) -> datetime: ...
