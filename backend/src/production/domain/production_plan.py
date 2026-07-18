"""Validated production plan contract."""

import math

from pydantic import Field, model_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.scene_plan import ScenePlan


class ProductionPlan(ContractModel):
    """Versioned creative and technical plan for a long-form production."""

    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    title: str = Field(min_length=1)
    original_prompt: str = Field(min_length=1)
    target_platform: str = Field(min_length=1)
    language: str = Field(min_length=2)
    duration_seconds: float = Field(gt=0)
    aspect_ratio: str = Field(pattern=r"^\d+:\d+$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0, le=240)
    style: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    narration_style: str = Field(min_length=1)
    music_style: str = Field(min_length=1)
    generate_clips_after_render: bool = False
    scenes: list[ScenePlan] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_scene_collection(self) -> "ProductionPlan":
        orders = [scene.order for scene in self.scenes]
        if len(orders) != len(set(orders)):
            raise ValueError("scene order values must be unique")

        scene_ids = [scene.scene_id for scene in self.scenes]
        if len(scene_ids) != len(set(scene_ids)):
            raise ValueError("scene_id values must be unique")

        scene_duration = sum(scene.duration_seconds for scene in self.scenes)
        if not math.isclose(scene_duration, self.duration_seconds, abs_tol=0.01):
            raise ValueError("duration_seconds must equal the total scene duration")
        return self
