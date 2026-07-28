"""Provider-neutral ports and read models for durable speech generation."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from pydantic import Field, field_validator

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.path_rules import validate_relative_path
from backend.src.production.scripting.models import ProductionScript
from backend.src.production.speech_generation.configuration import (
    SpeechGenerationConfiguration,
)
from backend.src.production.speech_generation.models import (
    ReadSpeechBinaryAsset,
    SpeechAudioWriteRequest,
    SpeechBinaryAsset,
    SpeechGenerationManifest,
    SpeechSegmentAudioMetadata,
    SpeechSegmentRequest,
)

if TYPE_CHECKING:
    from backend.src.production.runtime.context import StageContext


class ReadSpeechSourceScript(ContractModel):
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
    def aware_created_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("source script creation time must be timezone-aware")
        return value

    @field_validator("relative_path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        normalized = validate_relative_path(value)
        if "\\" in normalized:
            raise ValueError("source script path must use POSIX separators")
        return normalized


class SpeechSourceScriptReader(Protocol):
    async def read_for_speech_generation(
        self, *, context: StageContext
    ) -> ReadSpeechSourceScript: ...


class SpeechProviderRequest(ContractModel):
    job_id: UUID
    command_id: UUID
    correlation_id: UUID
    attempt_number: int = Field(ge=1)
    segment: SpeechSegmentRequest
    configuration: SpeechGenerationConfiguration
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class SpeechProviderResult(ContractModel):
    content: bytes = Field(repr=False, exclude=True, min_length=1)
    mime_type: str = "audio/wav"
    provider: str = Field(min_length=1, max_length=100)
    audio: SpeechSegmentAudioMetadata
    deterministic: bool
    metadata: dict[str, bool | int | str] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def safe_metadata(
        cls,
        value: dict[str, bool | int | str],
    ) -> dict[str, bool | int | str]:
        validated = validate_safe_json(value, path="speech_provider_result.metadata")
        if not isinstance(validated, dict):
            raise ValueError("speech provider metadata must be an object")
        return validated


class SpeechGenerationProvider(Protocol):
    name: str

    async def generate(self, request: SpeechProviderRequest) -> SpeechProviderResult: ...

    async def close(self) -> None: ...


class SpeechAudioStore(Protocol):
    async def write(
        self, *, request: SpeechAudioWriteRequest, content: bytes
    ) -> SpeechBinaryAsset: ...

    async def recover(self, *, request: SpeechAudioWriteRequest) -> SpeechBinaryAsset | None: ...

    async def resolve(self, *, job_id: UUID, segment_id: str) -> ReadSpeechBinaryAsset: ...

    async def read(self, *, asset: SpeechBinaryAsset) -> ReadSpeechBinaryAsset: ...


class SpeechManifestWriter(Protocol):
    async def read_existing(self, *, context: StageContext) -> SpeechGenerationManifest | None: ...

    async def create(
        self, *, context: StageContext, manifest: SpeechGenerationManifest
    ) -> None: ...

    async def checkpoint(
        self,
        *,
        context: StageContext,
        previous: SpeechGenerationManifest,
        current: SpeechGenerationManifest,
    ) -> None: ...

    async def finalize(
        self,
        *,
        context: StageContext,
        previous: SpeechGenerationManifest,
        current: SpeechGenerationManifest,
    ) -> None: ...
