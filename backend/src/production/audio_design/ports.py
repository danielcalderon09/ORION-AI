"""Provider-neutral ports and stable read models for audio design."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import Field, field_validator

from backend.src.production.audio_design.models import (
    AudioAssetExpectation,
    AudioDesignManifest,
    GeneratedAudioResult,
    MusicGenerationRequest,
    ReadStoredAudioDesignAsset,
    SoundEffectGenerationRequest,
    StoredAudioDesignAsset,
)
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.enums import ProductionStage
from backend.src.production.domain.path_rules import validate_relative_path
from backend.src.production.scripting.models import ProductionScript


class AudioDesignStageContext(Protocol):
    job_id: UUID
    command_id: UUID
    stage: ProductionStage
    attempt_number: int
    workspace_relative_path: str


class ReadAudioDesignSourceScript(ContractModel):
    script: ProductionScript
    artifact_id: UUID
    relative_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0)
    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    provider: str | None = None
    model_version: str | None = None
    created_at: datetime | None = None

    @field_validator("created_at")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("source script creation time must be timezone-aware")
        return value

    @field_validator("relative_path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        normalized = validate_relative_path(value)
        if "\\" in normalized:
            raise ValueError("source script path must use POSIX separators")
        return normalized


class AudioDesignSourceScriptReader(Protocol):
    async def read_for_audio_design(
        self,
        *,
        context: AudioDesignStageContext,
    ) -> ReadAudioDesignSourceScript: ...


class MusicGenerationProvider(Protocol):
    provider_id: str

    async def generate(self, request: MusicGenerationRequest) -> GeneratedAudioResult: ...

    async def close(self) -> None: ...


class SoundEffectGenerationProvider(Protocol):
    provider_id: str

    async def generate(
        self,
        request: SoundEffectGenerationRequest,
    ) -> GeneratedAudioResult: ...

    async def close(self) -> None: ...


class AudioDesignAssetStore(Protocol):
    async def write(
        self,
        *,
        expectation: AudioAssetExpectation,
        content: bytes,
    ) -> StoredAudioDesignAsset: ...

    async def recover(
        self,
        *,
        expectation: AudioAssetExpectation,
    ) -> StoredAudioDesignAsset | None: ...

    async def resolve(
        self,
        *,
        expectation: AudioAssetExpectation,
    ) -> ReadStoredAudioDesignAsset: ...

    async def read(
        self,
        *,
        asset: StoredAudioDesignAsset,
    ) -> ReadStoredAudioDesignAsset: ...


class AudioDesignManifestStore(Protocol):
    async def read_existing(
        self,
        *,
        context: AudioDesignStageContext,
    ) -> AudioDesignManifest | None: ...

    async def create(
        self,
        *,
        context: AudioDesignStageContext,
        manifest: AudioDesignManifest,
    ) -> None: ...

    async def checkpoint(
        self,
        *,
        context: AudioDesignStageContext,
        previous: AudioDesignManifest,
        current: AudioDesignManifest,
    ) -> None: ...

    async def finalize(
        self,
        *,
        context: AudioDesignStageContext,
        previous: AudioDesignManifest,
        current: AudioDesignManifest,
    ) -> None: ...
