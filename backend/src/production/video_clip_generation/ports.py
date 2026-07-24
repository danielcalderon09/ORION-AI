"""Provider-neutral ports for durable video clip generation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.enums import ArtifactType
from backend.src.production.image_acquisition.models import (
    ProductionImageAcquisitionManifest,
)
from backend.src.production.video_clip_generation.configuration import (
    VideoClipGenerationConfiguration,
)
from backend.src.production.video_clip_generation.models import (
    ProductionVideoClipAsset,
    ProductionVideoClipManifest,
    ReadProductionVideoClipAsset,
    VideoClipWriteRequest,
)

if TYPE_CHECKING:
    from backend.src.production.runtime.context import StageContext


class ImageManifestArtifactCandidate(ContractModel):
    artifact_id: UUID
    job_id: UUID
    artifact_type: ArtifactType
    relative_path: str
    size_bytes: int | None
    sha256: str | None
    provider: str | None
    model_version: str | None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceImageArtifactCandidate(ContractModel):
    artifact_id: UUID
    job_id: UUID
    artifact_type: ArtifactType
    relative_path: str
    mime_type: str
    size_bytes: int | None
    sha256: str | None
    width: int | None
    height: int | None
    metadata: dict[str, Any] = Field(default_factory=dict)


class InputArtifactIdentity(ContractModel):
    artifact_id: UUID
    job_id: UUID
    artifact_type: ArtifactType


class ImageAcquisitionManifestArtifactQueryRepository(Protocol):
    def list_candidates(self, *, job_id: UUID) -> tuple[ImageManifestArtifactCandidate, ...]: ...

    def list_input_artifacts(
        self, *, artifact_ids: tuple[UUID, ...]
    ) -> dict[UUID, InputArtifactIdentity]: ...

    def get_source_image(
        self, *, job_id: UUID, artifact_id: UUID
    ) -> SourceImageArtifactCandidate | None: ...


class VerifiedSourceImage(ContractModel):
    visual_asset_id: str = Field(
        pattern=r"^asset-s[0-9]{3}-q[0-9]{3}-v[0-9]{3}$"
    )
    artifact_id: UUID
    binary_asset_id: str
    sha256: str
    size_bytes: int
    mime_type: str
    width: int
    height: int
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    shot_id: str = Field(pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$")
    scene_number: int
    shot_number: int
    role: str
    content: bytes = Field(repr=False, exclude=True)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        result = validate_safe_json(value, path="verified_source_image.metadata")
        if not isinstance(result, dict):
            raise ValueError("verified source image metadata must be an object")
        return result

    @model_validator(mode="after")
    def validate_mapping(self) -> VerifiedSourceImage:
        if self.scene_id != f"scene-{self.scene_number:03d}":
            raise ValueError("verified source image scene mapping is inconsistent")
        if self.shot_id != (
            f"scene-{self.scene_number:03d}-shot-{self.shot_number:03d}"
        ):
            raise ValueError("verified source image shot mapping is inconsistent")
        return self


class ReadImageAcquisitionManifest(ContractModel):
    manifest: ProductionImageAcquisitionManifest
    job_id: UUID
    artifact_id: UUID
    sha256: str
    size_bytes: int
    schema_version: str
    source_images: tuple[VerifiedSourceImage, ...]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        result = validate_safe_json(value, path="read_image_manifest.metadata")
        if not isinstance(result, dict):
            raise ValueError("read image manifest metadata must be an object")
        return result

    @model_validator(mode="after")
    def validate_sources(self) -> ReadImageAcquisitionManifest:
        if self.schema_version != self.manifest.schema_version:
            raise ValueError("read image manifest schema version differs")
        expected = self.manifest.entries
        if tuple(image.visual_asset_id for image in self.source_images) != tuple(
            entry.visual_asset_id for entry in expected
        ):
            raise ValueError("verified source images differ from manifest entries")
        for image, entry in zip(self.source_images, expected, strict=True):
            if (
                image.artifact_id != entry.binary_artifact_id
                or image.binary_asset_id != entry.binary_asset_id
                or image.sha256 != entry.sha256
                or image.size_bytes != entry.size_bytes
                or image.mime_type != entry.mime_type
                or image.width != entry.width
                or image.height != entry.height
                or image.scene_id != entry.source_scene_id
                or image.shot_id != entry.source_shot_id
                or image.scene_number != entry.scene_number
                or image.shot_number != entry.shot_number
                or image.role != entry.role.value
            ):
                raise ValueError(
                    "verified source image metadata differs from manifest entry"
                )
        return self


class ImageAcquisitionManifestReader(Protocol):
    async def read_for_video_clip_generation(
        self, *, context: StageContext
    ) -> ReadImageAcquisitionManifest: ...


class VideoClipProviderRequest(ContractModel):
    job_id: UUID
    command_id: UUID
    correlation_id: UUID
    attempt_number: int = Field(ge=1)
    visual_asset_id: str
    source_image_artifact_id: UUID
    source_image_sha256: str
    source_image_mime_type: str
    source_image_content: bytes = Field(repr=False, exclude=True, min_length=1)
    duration_seconds: float = Field(gt=0, le=10)
    frame_rate: int = Field(gt=0, le=120)
    width: int = Field(gt=0, le=16_384)
    height: int = Field(gt=0, le=16_384)
    configuration: VideoClipGenerationConfiguration
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class GeneratedVideoClipPayload(ContractModel):
    content: bytes = Field(repr=False, exclude=True, min_length=1)
    mime_type: str
    index: int = Field(ge=0, le=9)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider_metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        result = validate_safe_json(value, path="generated_video_clip.metadata")
        if not isinstance(result, dict):
            raise ValueError("generated video clip metadata must be an object")
        return result


class VideoClipProviderResponse(ContractModel):
    clips: tuple[GeneratedVideoClipPayload, ...] = Field(min_length=1, max_length=1)
    provider: str
    requested_model: str
    reported_model: str
    request_id: str | None = None
    latency_ms: float = Field(ge=0)
    cost_usd: Decimal | None = Field(default=None, ge=0)
    finish_reason: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        result = validate_safe_json(value, path="video_clip_provider.metadata")
        if not isinstance(result, dict):
            raise ValueError("video clip provider metadata must be an object")
        return result


class VideoClipGenerationProvider(Protocol):
    async def generate_clip(
        self, request: VideoClipProviderRequest
    ) -> VideoClipProviderResponse: ...

    async def close(self) -> None: ...


class VideoClipBinaryStore(Protocol):
    async def write(
        self, *, request: VideoClipWriteRequest, content: bytes
    ) -> ProductionVideoClipAsset: ...

    async def resolve(
        self, *, job_id: UUID, visual_asset_id: str
    ) -> ReadProductionVideoClipAsset: ...

    async def read(
        self, *, asset: ProductionVideoClipAsset
    ) -> ReadProductionVideoClipAsset: ...


class VideoClipManifestWriter(Protocol):
    async def read_existing(
        self, *, context: StageContext
    ) -> ProductionVideoClipManifest | None: ...

    async def create(
        self, *, context: StageContext, manifest: ProductionVideoClipManifest
    ) -> None: ...

    async def checkpoint(
        self,
        *,
        context: StageContext,
        previous: ProductionVideoClipManifest,
        current: ProductionVideoClipManifest,
    ) -> None: ...

    async def finalize(
        self,
        *,
        context: StageContext,
        previous: ProductionVideoClipManifest,
        current: ProductionVideoClipManifest,
    ) -> None: ...
