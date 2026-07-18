"""Editor-independent timeline and render contract."""

import math
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.enums import (
    ArtifactType,
    AssetType,
    MotionType,
    TransitionType,
)
from backend.src.production.domain.path_rules import validate_relative_path


class EditScene(ContractModel):
    """Concrete scene ready to be placed on an editor timeline."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    scene_id: UUID
    order: int = Field(ge=0)
    duration_seconds: float = Field(gt=0)
    asset_artifact_id: UUID
    asset_type: AssetType
    narration_text: str = Field(min_length=1)
    motion: MotionType
    transition: TransitionType
    on_screen_text: str | None = None


class EditPackage(ContractModel):
    """Complete, editor-agnostic input for a future ``EditorPort`` adapter."""

    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    job_id: UUID
    project_name: str = Field(min_length=1)
    timeline_name: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0, le=240)
    duration_seconds: float = Field(gt=0)
    scenes: list[EditScene] = Field(min_length=1)
    artifacts: list[Artifact] = Field(min_length=1)
    narration_artifact: UUID | None = None
    music_artifact: UUID | None = None
    subtitle_artifact: UUID | None = None
    output_relative_path: str
    render_preset: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("output_relative_path")
    @classmethod
    def validate_output_path(cls, value: str) -> str:
        return validate_relative_path(value)

    @model_validator(mode="after")
    def validate_contract(self) -> "EditPackage":
        orders = [scene.order for scene in self.scenes]
        if len(orders) != len(set(orders)):
            raise ValueError("scene order values must be unique")

        scene_ids = [scene.scene_id for scene in self.scenes]
        if len(scene_ids) != len(set(scene_ids)):
            raise ValueError("scene_id values must be unique")

        scene_duration = sum(scene.duration_seconds for scene in self.scenes)
        if not math.isclose(scene_duration, self.duration_seconds, abs_tol=0.01):
            raise ValueError("duration_seconds must equal the total scene duration")

        artifact_by_id = {artifact.artifact_id: artifact for artifact in self.artifacts}
        if len(artifact_by_id) != len(self.artifacts):
            raise ValueError("artifact_id values must be unique")
        if any(artifact.job_id != self.job_id for artifact in self.artifacts):
            raise ValueError("all artifacts must belong to the edit package job")

        for scene in self.scenes:
            artifact = artifact_by_id.get(scene.asset_artifact_id)
            if artifact is None:
                raise ValueError("every scene asset must reference a declared artifact")
            expected_type = (
                ArtifactType.SOURCE_IMAGE
                if scene.asset_type is AssetType.IMAGE
                else ArtifactType.SOURCE_VIDEO
            )
            if artifact.artifact_type is not expected_type:
                raise ValueError("scene asset_type must match its artifact type")

        role_types = {
            "narration_artifact": ArtifactType.NARRATION,
            "music_artifact": ArtifactType.MUSIC,
            "subtitle_artifact": ArtifactType.SUBTITLES,
        }
        for field_name, artifact_type in role_types.items():
            artifact_id = getattr(self, field_name)
            if artifact_id is None:
                continue
            artifact = artifact_by_id.get(artifact_id)
            if artifact is None or artifact.artifact_type is not artifact_type:
                raise ValueError(f"{field_name} must reference a declared {artifact_type.value}")
        return self
