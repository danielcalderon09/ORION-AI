"""Provider-neutral contract for one bounded narration-only expansion pass."""

from __future__ import annotations

import hashlib
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.planning.validation import validate_planning_text
from backend.src.production.scripting.duration_policy import (
    ScriptingDurationAssessment,
    narration_word_count,
)
from backend.src.production.scripting.models import ProductionScript
from backend.src.production.scripting.serialization import serialize_production_script


class NarrationExpansionSourceScene(ContractModel):
    source_scene_number: int = Field(ge=1, le=50)
    original_narration: str = Field(min_length=1, max_length=6_000)
    maximum_words: int = Field(ge=2, le=1_000)
    semantic_constraints: tuple[str, ...] = Field(min_length=1, max_length=20)

    @field_validator("original_narration")
    @classmethod
    def validate_narration(cls, value: str) -> str:
        return validate_planning_text(value)

    @field_validator("semantic_constraints")
    @classmethod
    def validate_constraints(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(validate_planning_text(item) for item in value)


class NarrationExpansionRequest(ContractModel):
    schema_version: str = "1.0.0"
    job_id: UUID
    source_script_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    language: str = Field(min_length=2, max_length=16)
    target_duration_ms: int = Field(gt=0, le=60_000)
    minimum_duration_ms: int = Field(gt=0, le=60_000)
    ideal_duration_ms: int = Field(gt=0, le=60_000)
    reading_speed_words_per_minute: int = Field(ge=80, le=240)
    maximum_total_words: int = Field(ge=2, le=1_000)
    source_word_count: int = Field(ge=1)
    source_punctuation_count: int = Field(ge=0)
    source_estimated_duration_ms: int = Field(gt=0)
    scenes: tuple[NarrationExpansionSourceScene, ...] = Field(min_length=1, max_length=50)
    required_semantic_constraints: tuple[str, ...] = Field(min_length=1, max_length=20)

    @field_validator("required_semantic_constraints")
    @classmethod
    def validate_constraints(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(validate_planning_text(item) for item in value)

    @model_validator(mode="after")
    def validate_request(self) -> NarrationExpansionRequest:
        identities = tuple(scene.source_scene_number for scene in self.scenes)
        if len(identities) != len(set(identities)):
            raise ValueError("expansion source scene identities must be unique")
        if sum(scene.maximum_words for scene in self.scenes) != self.maximum_total_words:
            raise ValueError("expansion scene budgets must equal the total budget")
        if not self.minimum_duration_ms <= self.ideal_duration_ms <= self.target_duration_ms:
            raise ValueError("expansion duration window is invalid")
        return self


class NarrationExpansionScene(ContractModel):
    source_scene_number: int = Field(ge=1, le=50)
    narration: str = Field(min_length=1, max_length=6_000)

    @field_validator("narration")
    @classmethod
    def validate_narration(cls, value: str) -> str:
        return validate_planning_text(value)


class NarrationExpansionResponse(ContractModel):
    schema_version: str = "1.0.0"
    language: str = Field(min_length=2, max_length=16)
    scenes: tuple[NarrationExpansionScene, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_scene_identities(self) -> NarrationExpansionResponse:
        identities = tuple(scene.source_scene_number for scene in self.scenes)
        if len(identities) != len(set(identities)):
            raise ValueError("expanded scene identities must be unique")
        return self


def narration_expansion_request(
    *,
    job_id: UUID,
    source_script: ProductionScript,
    assessment: ScriptingDurationAssessment,
    minimum_duration_ms: int,
    ideal_duration_ms: int,
    maximum_total_words: int,
    scene_word_budgets: tuple[int, ...],
) -> NarrationExpansionRequest:
    if len(scene_word_budgets) != len(source_script.scenes):
        raise ValueError("expansion scene budget count differs")
    return NarrationExpansionRequest(
        job_id=job_id,
        source_script_sha256=hashlib.sha256(
            serialize_production_script(source_script)
        ).hexdigest(),
        language=source_script.language,
        target_duration_ms=assessment.target_duration_ms,
        minimum_duration_ms=minimum_duration_ms,
        ideal_duration_ms=ideal_duration_ms,
        reading_speed_words_per_minute=assessment.reading_speed_words_per_minute,
        maximum_total_words=maximum_total_words,
        source_word_count=assessment.narration_word_count,
        source_punctuation_count=assessment.punctuation_count,
        source_estimated_duration_ms=assessment.estimated_duration_ms,
        scenes=tuple(
            NarrationExpansionSourceScene(
                source_scene_number=scene.source_scene_number,
                original_narration=scene.narration,
                maximum_words=maximum_words,
                semantic_constraints=_semantic_constraints(source_script, index),
            )
            for index, (scene, maximum_words) in enumerate(
                zip(source_script.scenes, scene_word_budgets, strict=True)
            )
        ),
        required_semantic_constraints=(
            "Preserve the original meaning, facts, language, and scene order.",
            "Add only useful explanatory detail supported by the existing script.",
            "Do not repeat phrases, invent facts, or add empty filler.",
        ),
    )


def merge_narration_expansion(
    *,
    source_script: ProductionScript,
    request: NarrationExpansionRequest,
    response: NarrationExpansionResponse,
) -> ProductionScript:
    actual_source_hash = hashlib.sha256(
        serialize_production_script(source_script)
    ).hexdigest()
    if actual_source_hash != request.source_script_sha256:
        raise ValueError("expansion source script hash differs")
    if response.language != request.language:
        raise ValueError("expansion response language differs")
    expected = tuple(scene.source_scene_number for scene in request.scenes)
    actual = tuple(scene.source_scene_number for scene in response.scenes)
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise ValueError("expanded scenes do not match the source script")
    response_by_source = {scene.source_scene_number: scene for scene in response.scenes}
    budget_by_source = {scene.source_scene_number: scene.maximum_words for scene in request.scenes}
    merged_scenes = []
    for source_scene in source_script.scenes:
        expanded = response_by_source[source_scene.source_scene_number]
        if narration_word_count(expanded.narration) > budget_by_source[
            source_scene.source_scene_number
        ]:
            raise ValueError("expanded scene exceeds its word budget")
        merged_scenes.append(source_scene.model_copy(update={"narration": expanded.narration}))
    return ProductionScript.model_validate(
        source_script.model_copy(update={"scenes": tuple(merged_scenes)}).model_dump(
            mode="python"
        )
    )


def _semantic_constraints(
    source_script: ProductionScript, index: int
) -> tuple[str, ...]:
    scene = source_script.scenes[index]
    values = [scene.heading, scene.visual_intent]
    if scene.story_beat is not None:
        values.extend(
            (
                scene.story_beat.information_introduced,
                scene.story_beat.new_information,
                scene.story_beat.transition_intent,
            )
        )
    if index == 0:
        values.append(source_script.opening_hook)
    if index == len(source_script.scenes) - 1 and source_script.narrative_arc is not None:
        values.append(source_script.narrative_arc.intended_payoff)
    return tuple(dict.fromkeys(values))


__all__ = [
    "NarrationExpansionRequest",
    "NarrationExpansionResponse",
    "NarrationExpansionScene",
    "NarrationExpansionSourceScene",
    "merge_narration_expansion",
    "narration_expansion_request",
]
