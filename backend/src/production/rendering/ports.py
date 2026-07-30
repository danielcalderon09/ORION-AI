"""Small renderer-neutral and persistence boundaries."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from backend.src.production.domain.artifact import Artifact
from backend.src.production.media_composition.domain.models import (
    MediaCompositionManifest,
    MediaCompositionPlan,
)
from backend.src.production.rendering.models import (
    DryRunRenderResult,
    LocalRenderRequest,
    RendererCapabilities,
    RendererKind,
    RenderExecutionManifest,
)


class RenderStageContext(Protocol):
    job_id: UUID
    attempt_number: int
    workspace_relative_path: str


class RenderArtifactInventory(Protocol):
    async def list_for_job(self, job_id: UUID) -> tuple[Artifact, ...]: ...


class VerifiedCompositionSource:
    def __init__(
        self,
        *,
        plan_artifact: Artifact,
        manifest_artifact: Artifact,
        plan: MediaCompositionPlan,
        manifest: MediaCompositionManifest,
    ) -> None:
        self.plan_artifact = plan_artifact
        self.manifest_artifact = manifest_artifact
        self.plan = plan
        self.manifest = manifest


class RenderCompositionSourceReader(Protocol):
    async def read(
        self,
        *,
        context: RenderStageContext,
    ) -> VerifiedCompositionSource: ...


class LocalRenderStore(Protocol):
    async def read_request(
        self,
        *,
        context: RenderStageContext,
    ) -> LocalRenderRequest | None: ...

    async def write_request(
        self,
        *,
        context: RenderStageContext,
        request: LocalRenderRequest,
    ) -> tuple[str, int, str]: ...

    async def read_manifest(
        self,
        *,
        context: RenderStageContext,
    ) -> RenderExecutionManifest | None: ...

    async def create_manifest(
        self,
        *,
        context: RenderStageContext,
        manifest: RenderExecutionManifest,
    ) -> None: ...

    async def checkpoint_manifest(
        self,
        *,
        context: RenderStageContext,
        previous: RenderExecutionManifest,
        current: RenderExecutionManifest,
    ) -> None: ...

    async def output_exists(self, *, relative_path: str) -> bool: ...


class LocalRenderer(Protocol):
    @property
    def renderer_kind(self) -> RendererKind: ...

    @property
    def capabilities(self) -> RendererCapabilities: ...

    async def prepare_or_validate(
        self,
        request: LocalRenderRequest,
    ) -> DryRunRenderResult: ...

    async def close(self) -> None: ...


class RenderClock(Protocol):
    def __call__(self) -> datetime: ...
