"""Deterministic render-preparation fixtures."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from backend.src.production.application.commands import StageCommand
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.enums import (
    ArtifactStatus,
    ArtifactType,
    ProductionStage,
)
from backend.src.production.media_composition.configuration import (
    MediaCompositionConfiguration,
)
from backend.src.production.media_composition.domain.timeline import (
    build_media_composition_plan,
)
from backend.src.production.media_composition.ports import MediaCompositionSource
from backend.src.production.media_composition.recovery import project_manifest
from backend.src.production.media_composition.serialization import (
    serialize_media_composition_manifest,
    serialize_media_composition_plan,
)
from backend.src.production.rendering.ports import VerifiedCompositionSource
from backend.src.production.runtime.context import StageContext
from backend.tests.unit.production.media_composition.conftest import (
    JOB_ID,
    make_source,
)

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
COMMAND_ID = UUID("20000000-0000-4000-8000-000000000951")


def make_verified_source(
    configuration: MediaCompositionConfiguration | None = None,
    composition_source: MediaCompositionSource | None = None,
) -> VerifiedCompositionSource:
    source = composition_source or make_source()
    plan = build_media_composition_plan(
        source,
        configuration or MediaCompositionConfiguration(),
    )
    plan_path = f"production/{JOB_ID}/building_timeline/attempt-1/media-composition-plan.json"
    plan_content = serialize_media_composition_plan(plan)
    import hashlib

    plan_sha = hashlib.sha256(plan_content).hexdigest()
    manifest = project_manifest(
        plan=plan,
        source=source,
        attempt_number=1,
        plan_relative_path=plan_path,
        plan_sha256=plan_sha,
        plan_size_bytes=len(plan_content),
        now=NOW,
        existing=None,
    )
    manifest_path = (
        f"production/{JOB_ID}/building_timeline/attempt-1/media-composition-manifest.json"
    )
    manifest_content = serialize_media_composition_manifest(manifest)
    plan_artifact = Artifact(
        artifact_id=UUID("50000000-0000-4000-8000-000000000951"),
        job_id=JOB_ID,
        artifact_type=ArtifactType.MEDIA_COMPOSITION_PLAN,
        relative_path=plan_path,
        mime_type="application/json",
        status=ArtifactStatus.READY,
        size_bytes=len(plan_content),
        sha256=plan_sha,
        provider="orion-media-composition",
        model_version=plan.schema_version,
        metadata={
            "plan_fingerprint": plan.plan_fingerprint,
            "renderer_executed": False,
            "timeline_checksum": plan.timeline_checksum,
        },
    )
    manifest_artifact = Artifact(
        artifact_id=UUID("50000000-0000-4000-8000-000000000952"),
        job_id=JOB_ID,
        artifact_type=ArtifactType.MEDIA_COMPOSITION_MANIFEST,
        relative_path=manifest_path,
        mime_type="application/json",
        status=ArtifactStatus.READY,
        size_bytes=len(manifest_content),
        sha256=hashlib.sha256(manifest_content).hexdigest(),
        provider="orion-media-composition",
        model_version=manifest.schema_version,
        metadata={
            "plan_fingerprint": plan.plan_fingerprint,
            "renderer_executed": False,
            "status": manifest.status.value,
            "timeline_checksum": plan.timeline_checksum,
        },
    )
    return VerifiedCompositionSource(
        plan_artifact=plan_artifact,
        manifest_artifact=manifest_artifact,
        plan=plan,
        manifest=manifest,
    )


def write_verified_source(
    workspace: Path,
    source: VerifiedCompositionSource,
) -> tuple[Artifact, Artifact]:
    plan_content = serialize_media_composition_plan(source.plan)
    manifest_content = serialize_media_composition_manifest(source.manifest)
    for artifact, content in (
        (source.plan_artifact, plan_content),
        (source.manifest_artifact, manifest_content),
    ):
        target = workspace.joinpath(*artifact.relative_path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return source.plan_artifact, source.manifest_artifact


def make_render_command_context(
    *,
    attempt_number: int = 2,
) -> tuple[StageCommand, StageContext]:
    command = StageCommand(
        command_id=COMMAND_ID,
        job_id=JOB_ID,
        stage=ProductionStage.RENDERING_LONG_FORM,
        attempt_number=attempt_number,
        idempotency_key="render-preparation:test",
        created_at=NOW,
    )
    context = StageContext(
        job_id=JOB_ID,
        command_id=COMMAND_ID,
        stage=ProductionStage.RENDERING_LONG_FORM,
        attempt_number=attempt_number,
        workspace_relative_path=(f"production/{JOB_ID}/rendering/attempt-{attempt_number}"),
        correlation_id=JOB_ID,
    )
    return command, context


@dataclass
class StaticVerifiedSourceReader:
    source: VerifiedCompositionSource

    async def read(self, *, context: object) -> VerifiedCompositionSource:
        del context
        return self.source


@dataclass
class StaticArtifactInventory:
    artifacts: tuple[Artifact, ...]

    async def list_for_job(self, job_id: UUID) -> tuple[Artifact, ...]:
        return tuple(item for item in self.artifacts if item.job_id == job_id)
