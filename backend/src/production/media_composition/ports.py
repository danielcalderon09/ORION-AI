"""Provider-neutral ports and normalized inputs for media composition."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import Field, model_validator

from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.base import ContractModel
from backend.src.production.media_composition.domain.models import (
    CompositionAssetReference,
    CompositionAssetValidation,
    CompositionTransitionKind,
    MediaCompositionManifest,
    MediaCompositionPlan,
    SourceManifestReference,
)


class CompositionShotSource(ContractModel):
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    shot_id: str = Field(pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$")
    scene_number: int = Field(ge=1, le=50)
    shot_number: int = Field(ge=1, le=100)
    scene_start_ms: int = Field(ge=0, le=3_600_000)
    shot_start_ms: int = Field(ge=0, le=3_600_000)
    shot_end_ms: int = Field(gt=0, le=3_600_000)
    transition_kind: CompositionTransitionKind
    transition_duration_ms: int = Field(ge=0, le=10_000)
    video_asset_id: str


class CompositionNarrationSource(ContractModel):
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    sequence_index: int = Field(ge=0, le=49)
    timeline_start_ms: int = Field(ge=0, le=3_600_000)
    duration_ms: int = Field(gt=0, le=600_000)
    asset_id: str


class CompositionMusicSource(ContractModel):
    requirement_id: str = Field(pattern=r"^music-[a-f0-9]{24}$")
    duration_ms: int = Field(gt=0, le=600_000)
    duck_under_narration: bool
    asset_id: str


class CompositionSoundEffectSource(ContractModel):
    requirement_id: str = Field(pattern=r"^sfx-[a-f0-9]{24}$")
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    shot_id: str | None = None
    target_offset_ms: int = Field(ge=0, le=3_600_000)
    duration_ms: int = Field(gt=0, le=30_000)
    asset_id: str


class CompositionSubtitleSource(ContractModel):
    asset_id: str
    cue_start_ms: tuple[int, ...] = Field(default=(), max_length=10_000)
    cue_end_ms: tuple[int, ...] = Field(default=(), max_length=10_000)
    cue_text_sha256: tuple[str, ...] = Field(default=(), max_length=10_000)

    @model_validator(mode="after")
    def matching_cue_arrays(self) -> CompositionSubtitleSource:
        if not (len(self.cue_start_ms) == len(self.cue_end_ms) == len(self.cue_text_sha256)):
            raise ValueError("subtitle source cue arrays must have equal length")
        return self


class MediaCompositionSource(ContractModel):
    job_id: UUID
    source_manifests: tuple[SourceManifestReference, ...]
    assets: tuple[CompositionAssetReference, ...]
    asset_validation: tuple[CompositionAssetValidation, ...]
    shots: tuple[CompositionShotSource, ...] = Field(min_length=1, max_length=5_000)
    narration: tuple[CompositionNarrationSource, ...] = Field(
        min_length=1,
        max_length=50,
    )
    music: CompositionMusicSource | None = None
    sound_effects: tuple[CompositionSoundEffectSource, ...] = Field(
        default=(),
        max_length=500,
    )
    subtitles: CompositionSubtitleSource | None = None
    orphan_asset_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_source_inventory(self) -> MediaCompositionSource:
        asset_ids = tuple(item.asset_id for item in self.assets)
        validation_ids = tuple(item.asset_id for item in self.asset_validation)
        if asset_ids != tuple(sorted(asset_ids)) or len(asset_ids) != len(set(asset_ids)):
            raise ValueError("source assets must be unique and sorted")
        if validation_ids != asset_ids:
            raise ValueError("source asset validation must match asset ordering")
        manifest_types = tuple(item.artifact_type.value for item in self.source_manifests)
        if manifest_types != tuple(sorted(manifest_types)) or len(manifest_types) != len(
            set(manifest_types)
        ):
            raise ValueError("source manifests must be unique and sorted")
        shot_ids = tuple(item.shot_id for item in self.shots)
        if len(shot_ids) != len(set(shot_ids)):
            raise ValueError("composition source shots must be unique")
        return self


class MediaCompositionStageContext(Protocol):
    job_id: UUID
    attempt_number: int
    workspace_relative_path: str


class MediaCompositionSourceReader(Protocol):
    async def read(
        self,
        *,
        context: MediaCompositionStageContext,
    ) -> MediaCompositionSource: ...


class MediaCompositionArtifactInventory(Protocol):
    async def list_for_job(self, job_id: UUID) -> tuple[Artifact, ...]: ...


class MediaCompositionStore(Protocol):
    async def read_plan(
        self,
        *,
        context: MediaCompositionStageContext,
    ) -> MediaCompositionPlan | None: ...

    async def write_plan(
        self,
        *,
        context: MediaCompositionStageContext,
        plan: MediaCompositionPlan,
    ) -> tuple[str, int, str]: ...

    async def read_manifest(
        self,
        *,
        context: MediaCompositionStageContext,
    ) -> MediaCompositionManifest | None: ...

    async def create_manifest(
        self,
        *,
        context: MediaCompositionStageContext,
        manifest: MediaCompositionManifest,
    ) -> None: ...

    async def checkpoint_manifest(
        self,
        *,
        context: MediaCompositionStageContext,
        previous: MediaCompositionManifest,
        current: MediaCompositionManifest,
    ) -> None: ...


class CompositionClock(Protocol):
    def __call__(self) -> datetime: ...
