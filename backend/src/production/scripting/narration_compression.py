"""Provider-neutral contracts for one bounded narration-compression pass."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.planning.validation import validate_planning_text
from backend.src.production.scripting.duration_policy import (
    ScriptingDurationAssessment,
    narration_word_count,
)
from backend.src.production.scripting.models import ProductionScript


class NarrationCompressionSourceScene(ContractModel):
    source_scene_number: int = Field(ge=1, le=50)
    original_narration: str = Field(min_length=1, max_length=6000)
    maximum_words: int = Field(ge=2, le=1000)
    semantic_constraints: tuple[str, ...] = Field(min_length=1, max_length=20)

    @field_validator("original_narration")
    @classmethod
    def validate_narration(cls, value: str) -> str:
        return validate_planning_text(value)

    @field_validator("semantic_constraints")
    @classmethod
    def validate_constraints(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(validate_planning_text(item) for item in value)


class NarrationCompressionRequest(ContractModel):
    schema_version: str = "1.0.0"
    job_id: UUID
    language: str = Field(min_length=2, max_length=16)
    target_duration_ms: int = Field(gt=0, le=60_000)
    reading_speed_words_per_minute: int = Field(ge=80, le=240)
    maximum_total_words: int = Field(ge=2, le=1000)
    source_word_count: int = Field(ge=1)
    source_punctuation_count: int = Field(ge=0)
    source_estimated_duration_ms: int = Field(gt=0)
    scenes: tuple[NarrationCompressionSourceScene, ...] = Field(min_length=1, max_length=50)
    required_semantic_constraints: tuple[str, ...] = Field(min_length=1, max_length=20)

    @field_validator("required_semantic_constraints")
    @classmethod
    def validate_constraints(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(validate_planning_text(item) for item in value)

    @model_validator(mode="after")
    def validate_request(self) -> NarrationCompressionRequest:
        identities = tuple(scene.source_scene_number for scene in self.scenes)
        if len(identities) != len(set(identities)):
            raise ValueError("compression source scene identities must be unique")
        if sum(scene.maximum_words for scene in self.scenes) != self.maximum_total_words:
            raise ValueError("compression scene budgets must equal the total budget")
        return self


class NarrationCompressionScene(ContractModel):
    source_scene_number: int = Field(ge=1, le=50)
    narration: str = Field(min_length=1, max_length=6000)

    @field_validator("narration")
    @classmethod
    def validate_narration(cls, value: str) -> str:
        return validate_planning_text(value)


class NarrationCompressionResponse(ContractModel):
    schema_version: str = "1.0.0"
    scenes: tuple[NarrationCompressionScene, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_scene_identities(self) -> NarrationCompressionResponse:
        identities = tuple(scene.source_scene_number for scene in self.scenes)
        if len(identities) != len(set(identities)):
            raise ValueError("compressed scene identities must be unique")
        return self


def narration_compression_request(
    *,
    job_id: UUID,
    source_script: ProductionScript,
    assessment: ScriptingDurationAssessment,
    maximum_total_words: int,
    scene_word_budgets: tuple[int, ...],
) -> NarrationCompressionRequest:
    """Build the narrow request while retaining only semantic compression inputs."""

    if len(scene_word_budgets) != len(source_script.scenes):
        raise ValueError("compression scene budget count differs")
    source_scenes = tuple(
        NarrationCompressionSourceScene(
            source_scene_number=scene.source_scene_number,
            original_narration=scene.narration,
            maximum_words=maximum_words,
            semantic_constraints=_scene_semantic_constraints(source_script, index),
        )
        for index, (scene, maximum_words) in enumerate(
            zip(source_script.scenes, scene_word_budgets, strict=True)
        )
    )
    return NarrationCompressionRequest(
        job_id=job_id,
        language=source_script.language,
        target_duration_ms=assessment.target_duration_ms,
        reading_speed_words_per_minute=assessment.reading_speed_words_per_minute,
        maximum_total_words=maximum_total_words,
        source_word_count=assessment.narration_word_count,
        source_punctuation_count=assessment.punctuation_count,
        source_estimated_duration_ms=assessment.estimated_duration_ms,
        scenes=source_scenes,
        required_semantic_constraints=(
            "Preserve the original meaning, facts, and scene order.",
            "Do not introduce new facts or explanations.",
            "Keep every narration in the requested language.",
        ),
    )


def merge_narration_compression(
    *,
    source_script: ProductionScript,
    request: NarrationCompressionRequest,
    response: NarrationCompressionResponse,
) -> ProductionScript:
    """Replace narration only after exact identity and per-scene cap validation."""

    expected = tuple(scene.source_scene_number for scene in request.scenes)
    actual = tuple(scene.source_scene_number for scene in response.scenes)
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise ValueError("compressed scenes do not match the source script")
    response_by_source = {scene.source_scene_number: scene for scene in response.scenes}
    budget_by_source = {scene.source_scene_number: scene.maximum_words for scene in request.scenes}
    merged_scenes = []
    for source_scene in source_script.scenes:
        compressed = response_by_source[source_scene.source_scene_number]
        if narration_word_count(compressed.narration) > budget_by_source[
            source_scene.source_scene_number
        ]:
            raise ValueError("compressed scene exceeds its word budget")
        merged_scenes.append(source_scene.model_copy(update={"narration": compressed.narration}))
    return ProductionScript.model_validate(
        source_script.model_copy(update={"scenes": tuple(merged_scenes)}).model_dump(
            mode="python"
        )
    )


def _scene_semantic_constraints(
    source_script: ProductionScript,
    index: int,
) -> tuple[str, ...]:
    scene = source_script.scenes[index]
    constraints = [scene.heading]
    if scene.story_beat is not None:
        constraints.extend(
            (
                scene.story_beat.information_introduced,
                scene.story_beat.new_information,
                scene.story_beat.transition_intent,
            )
        )
    if index == 0:
        constraints.append(source_script.opening_hook)
    if index == len(source_script.scenes) - 1 and source_script.narrative_arc is not None:
        constraints.append(source_script.narrative_arc.intended_payoff)
    return tuple(dict.fromkeys(constraints))


__all__ = [
    "NarrationCompressionRequest",
    "NarrationCompressionResponse",
    "NarrationCompressionScene",
    "NarrationCompressionSourceScene",
    "merge_narration_compression",
    "narration_compression_request",
]
