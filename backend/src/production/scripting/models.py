"""Strict, versioned ProductionScript contracts."""

import math
from typing import Any

from pydantic import Field, field_validator, model_validator

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.domain.base import ContractModel
from backend.src.production.planning.models import ProductionPlan
from backend.src.production.planning.validation import validate_planning_text


class ProductionScriptScene(ContractModel):
    scene_number: int = Field(ge=1, le=50)
    source_scene_number: int = Field(ge=1, le=50)
    heading: str = Field(min_length=1, max_length=200)
    narration: str = Field(min_length=1, max_length=6000)
    estimated_duration_seconds: float = Field(gt=0, le=600)
    delivery_style: str = Field(min_length=1, max_length=200)
    pronunciation_notes: tuple[str, ...] = Field(default=(), max_length=50)
    on_screen_text: str | None = Field(default=None, max_length=500)
    visual_intent: str = Field(min_length=1, max_length=2000)
    transition_note: str | None = Field(default=None, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "heading",
        "narration",
        "delivery_style",
        "on_screen_text",
        "visual_intent",
        "transition_note",
    )
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        return validate_planning_text(value) if value is not None else None

    @field_validator("pronunciation_notes")
    @classmethod
    def validate_notes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(validate_planning_text(item) for item in value)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        validated = validate_safe_json(value, path="script_scene.metadata")
        if not isinstance(validated, dict):
            raise ValueError("script scene metadata must be an object")
        return validated


class ProductionScript(ContractModel):
    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    source_plan_schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    title: str = Field(min_length=1, max_length=300)
    language: str = Field(min_length=2, max_length=16)
    target_duration_seconds: float = Field(gt=0, le=3600)
    tone: str = Field(min_length=1, max_length=100)
    opening_hook: str = Field(min_length=1, max_length=1000)
    closing_call_to_action: str | None = Field(default=None, max_length=1000)
    scenes: tuple[ProductionScriptScene, ...] = Field(min_length=1, max_length=50)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", "language", "tone", "opening_hook", "closing_call_to_action")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        return validate_planning_text(value) if value is not None else None

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        validated = validate_safe_json(value, path="script.metadata")
        if not isinstance(validated, dict):
            raise ValueError("script metadata must be an object")
        return validated

    @model_validator(mode="after")
    def validate_scene_collection(self) -> "ProductionScript":
        numbers = tuple(scene.scene_number for scene in self.scenes)
        if numbers != tuple(range(1, len(self.scenes) + 1)):
            raise ValueError("scene_number values must be consecutive starting at 1")
        sources = tuple(scene.source_scene_number for scene in self.scenes)
        if len(sources) != len(set(sources)):
            raise ValueError("source_scene_number values must be unique")
        total = sum(scene.estimated_duration_seconds for scene in self.scenes)
        if not math.isclose(total, self.target_duration_seconds, abs_tol=0.1):
            raise ValueError("script duration must equal the total scene duration")
        return self


def validate_script_against_plan(
    script: ProductionScript,
    plan: ProductionPlan,
) -> ProductionScript:
    """Enforce the provider-independent relationship to the durable source plan."""

    if script.source_plan_schema_version != plan.schema_version:
        raise ValueError("source plan schema version does not match")
    if script.language.casefold() != plan.language.casefold():
        raise ValueError("script language does not match production plan")
    if not math.isclose(
        script.target_duration_seconds,
        plan.target_duration_seconds,
        abs_tol=0.1,
    ):
        raise ValueError("script target duration does not match production plan")
    expected = tuple(scene.scene_number for scene in plan.scenes)
    actual = tuple(scene.source_scene_number for scene in script.scenes)
    if actual != expected:
        raise ValueError("script must contain one ordered scene for every plan scene")
    for script_scene, plan_scene in zip(script.scenes, plan.scenes, strict=True):
        if not math.isclose(
            script_scene.estimated_duration_seconds,
            plan_scene.estimated_duration_seconds,
            abs_tol=0.1,
        ):
            raise ValueError("script scene duration does not match source scene")
    return script
