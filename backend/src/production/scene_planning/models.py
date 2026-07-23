"""Strict provider-independent scene-planning contracts."""

import math
from typing import Literal

from pydantic import Field, field_validator, model_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.planning.validation import validate_planning_text
from backend.src.production.scripting.models import ProductionScript

Framing = Literal[
    "extreme_wide",
    "wide",
    "medium",
    "close_up",
    "extreme_close_up",
    "over_the_shoulder",
    "point_of_view",
]
CameraAngle = Literal["eye_level", "high", "low", "overhead", "dutch"]
CameraMovement = Literal["static", "pan", "tilt", "dolly", "truck", "crane", "handheld"]
TransitionKind = Literal["none", "cut", "dissolve", "fade", "wipe", "match_cut"]


class ProductionCamera(ContractModel):
    framing: Framing
    angle: CameraAngle = "eye_level"
    movement: CameraMovement = "static"
    lens_millimeters: int = Field(default=50, ge=8, le=300)
    subject: str = Field(min_length=1, max_length=500)

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str) -> str:
        return validate_planning_text(value)


class ProductionTiming(ContractModel):
    start_seconds: float = Field(ge=0, le=3600)
    duration_seconds: float = Field(gt=0, le=600)
    end_seconds: float = Field(gt=0, le=3600)

    @model_validator(mode="after")
    def validate_interval(self) -> "ProductionTiming":
        if not math.isclose(
            self.start_seconds + self.duration_seconds,
            self.end_seconds,
            abs_tol=0.001,
        ):
            raise ValueError("shot timing end must equal start plus duration")
        return self


class ProductionTransition(ContractModel):
    kind: TransitionKind
    duration_seconds: float = Field(default=0, ge=0, le=10)

    @model_validator(mode="after")
    def validate_duration(self) -> "ProductionTransition":
        if self.kind in {"none", "cut", "match_cut"} and self.duration_seconds != 0:
            raise ValueError("instant transitions must have zero duration")
        if self.kind in {"dissolve", "fade", "wipe"} and self.duration_seconds <= 0:
            raise ValueError("timed transitions must have positive duration")
        return self


class ProductionShot(ContractModel):
    shot_id: str = Field(pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$")
    shot_number: int = Field(ge=1, le=100)
    scene_number: int = Field(ge=1, le=50)
    objective: str = Field(min_length=1, max_length=1000)
    description: str = Field(min_length=1, max_length=3000)
    camera: ProductionCamera
    timing: ProductionTiming
    transition: ProductionTransition

    @field_validator("objective", "description")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return validate_planning_text(value)


class ProductionScene(ContractModel):
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    scene_number: int = Field(ge=1, le=50)
    source_scene_number: int = Field(ge=1, le=50)
    title: str = Field(min_length=1, max_length=300)
    narration: str = Field(min_length=1, max_length=6000)
    objective: str = Field(min_length=1, max_length=1000)
    estimated_duration_seconds: float = Field(gt=0, le=600)
    shots: tuple[ProductionShot, ...] = Field(min_length=1, max_length=100)

    @field_validator("title", "narration", "objective")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return validate_planning_text(value)

    @model_validator(mode="after")
    def validate_shots(self) -> "ProductionScene":
        if self.scene_id != f"scene-{self.scene_number:03d}":
            raise ValueError("scene ID must correspond to scene number")
        numbers = tuple(shot.shot_number for shot in self.shots)
        if numbers != tuple(range(1, len(self.shots) + 1)):
            raise ValueError("shot numbers must be consecutive starting at 1")
        if any(shot.scene_number != self.scene_number for shot in self.shots):
            raise ValueError("every shot must belong to its containing scene")
        for shot in self.shots:
            expected_id = (
                f"scene-{self.scene_number:03d}-shot-{shot.shot_number:03d}"
            )
            if shot.shot_id != expected_id:
                raise ValueError("shot ID must correspond to scene and shot numbers")
        if any(shot.transition.kind == "none" for shot in self.shots[:-1]):
            raise ValueError("only the last shot of a scene may use no transition")
        expected_start = 0.0
        for shot in self.shots:
            if not math.isclose(shot.timing.start_seconds, expected_start, abs_tol=0.001):
                raise ValueError("shot timings must be ordered and contiguous")
            expected_start = shot.timing.end_seconds
        if not math.isclose(
            expected_start,
            self.estimated_duration_seconds,
            abs_tol=0.001,
        ):
            raise ValueError("shots must cover the complete scene duration")
        return self


class ProductionScenePlan(ContractModel):
    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    source_script_schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    source_script_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    title: str = Field(min_length=1, max_length=300)
    language: str = Field(min_length=2, max_length=16)
    target_duration_seconds: float = Field(gt=0, le=3600)
    scenes: tuple[ProductionScene, ...] = Field(min_length=1, max_length=50)

    @field_validator("title", "language")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return validate_planning_text(value)

    @model_validator(mode="after")
    def validate_collection(self) -> "ProductionScenePlan":
        scene_numbers = tuple(scene.scene_number for scene in self.scenes)
        if scene_numbers != tuple(range(1, len(self.scenes) + 1)):
            raise ValueError("scene numbers must be consecutive starting at 1")
        scene_ids = tuple(scene.scene_id for scene in self.scenes)
        source_numbers = tuple(scene.source_scene_number for scene in self.scenes)
        shot_ids = tuple(shot.shot_id for scene in self.scenes for shot in scene.shots)
        if (
            len(scene_ids) != len(set(scene_ids))
            or len(source_numbers) != len(set(source_numbers))
            or len(shot_ids) != len(set(shot_ids))
        ):
            raise ValueError("scene and shot IDs must be unique")
        total = sum(scene.estimated_duration_seconds for scene in self.scenes)
        if not math.isclose(total, self.target_duration_seconds, abs_tol=0.1):
            raise ValueError("scene durations must equal target duration")
        for index, scene in enumerate(self.scenes):
            last_transition = scene.shots[-1].transition.kind
            if index == len(self.scenes) - 1 and last_transition != "none":
                raise ValueError("the final shot must use the none transition")
            if index < len(self.scenes) - 1 and last_transition == "none":
                raise ValueError("non-final scenes must transition to the next scene")
        return self


def validate_scene_plan_against_script(
    plan: ProductionScenePlan,
    script: ProductionScript,
    *,
    source_script_sha256: str | None = None,
) -> ProductionScenePlan:
    """Validate the complete immutable relationship to the approved script."""

    if plan.source_script_schema_version != script.schema_version:
        raise ValueError("source script schema version does not match")
    if (
        source_script_sha256 is not None
        and plan.source_script_sha256 != source_script_sha256
    ):
        raise ValueError("source script checksum does not match")
    if plan.title != script.title or plan.language.casefold() != script.language.casefold():
        raise ValueError("scene plan identity does not match production script")
    if not math.isclose(
        plan.target_duration_seconds,
        script.target_duration_seconds,
        abs_tol=0.1,
    ):
        raise ValueError("scene plan duration does not match production script")
    if len(plan.scenes) != len(script.scenes):
        raise ValueError("scene plan must contain one scene per script scene")
    for scene, source in zip(plan.scenes, script.scenes, strict=True):
        if scene.source_scene_number != source.scene_number:
            raise ValueError("scene source mapping is inconsistent")
        if scene.narration != source.narration:
            raise ValueError("scene narration must preserve the approved script")
        if not math.isclose(
            scene.estimated_duration_seconds,
            source.estimated_duration_seconds,
            abs_tol=0.1,
        ):
            raise ValueError("scene duration does not match its script scene")
    return plan
