"""Provider-neutral contract for one bounded narration-only expansion pass."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field, ValidationError, field_validator, model_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.planning.validation import validate_planning_text
from backend.src.production.scripting.duration_policy import (
    ScriptingDurationAssessment,
    narration_word_count,
)
from backend.src.production.scripting.models import ProductionScript
from backend.src.production.scripting.serialization import serialize_production_script


class NarrationExpansionFailureCode(StrEnum):
    """Stable, non-sensitive reasons for rejecting an expansion response."""

    SCHEMA_INVALID = "narration_expansion_schema_invalid"
    LANGUAGE_MISMATCH = "narration_expansion_language_mismatch"
    SCENE_MISSING = "narration_expansion_scene_missing"
    SCENE_DUPLICATE = "narration_expansion_scene_duplicate"
    SCENE_UNKNOWN = "narration_expansion_scene_unknown"
    EMPTY_NARRATION = "narration_expansion_empty_narration"
    UNSAFE_NARRATION = "narration_expansion_unsafe_narration"
    SCENE_BUDGET_EXCEEDED = "narration_expansion_scene_budget_exceeded"
    GLOBAL_BUDGET_EXCEEDED = "narration_expansion_global_budget_exceeded"
    SOURCE_MISMATCH = "narration_expansion_source_mismatch"
    MERGE_INVALID = "narration_expansion_merge_invalid"


_FAILURE_MESSAGES: dict[NarrationExpansionFailureCode, str] = {
    NarrationExpansionFailureCode.SCHEMA_INVALID: "narration expansion schema is invalid",
    NarrationExpansionFailureCode.LANGUAGE_MISMATCH: (
        "narration expansion language differs from the source script"
    ),
    NarrationExpansionFailureCode.SCENE_MISSING: (
        "narration expansion is missing a source scene"
    ),
    NarrationExpansionFailureCode.SCENE_DUPLICATE: (
        "narration expansion contains a duplicate source scene"
    ),
    NarrationExpansionFailureCode.SCENE_UNKNOWN: (
        "narration expansion contains an unknown source scene"
    ),
    NarrationExpansionFailureCode.EMPTY_NARRATION: (
        "narration expansion contains empty narration"
    ),
    NarrationExpansionFailureCode.UNSAFE_NARRATION: (
        "narration expansion contains unsafe narration"
    ),
    NarrationExpansionFailureCode.SCENE_BUDGET_EXCEEDED: (
        "narration expansion exceeds a scene word budget"
    ),
    NarrationExpansionFailureCode.GLOBAL_BUDGET_EXCEEDED: (
        "narration expansion exceeds the global word budget"
    ),
    NarrationExpansionFailureCode.SOURCE_MISMATCH: (
        "narration expansion source binding differs"
    ),
    NarrationExpansionFailureCode.MERGE_INVALID: (
        "narration expansion could not produce a valid script"
    ),
}


class NarrationExpansionContractError(ValueError):
    """A classified expansion rejection containing safe derived metrics only."""

    def __init__(
        self,
        code: NarrationExpansionFailureCode,
        *,
        metadata: dict[str, bool | int | str],
    ) -> None:
        super().__init__(_FAILURE_MESSAGES[code])
        self.code = code
        self.safe_message = _FAILURE_MESSAGES[code]
        self.safe_metadata = metadata


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
    metrics = narration_expansion_safe_metrics(request=request, response=response)
    actual_source_hash = hashlib.sha256(
        serialize_production_script(source_script)
    ).hexdigest()
    if actual_source_hash != request.source_script_sha256:
        raise NarrationExpansionContractError(
            NarrationExpansionFailureCode.SOURCE_MISMATCH,
            metadata=metrics,
        )
    if response.language != request.language:
        raise NarrationExpansionContractError(
            NarrationExpansionFailureCode.LANGUAGE_MISMATCH,
            metadata=metrics,
        )
    expected = tuple(scene.source_scene_number for scene in request.scenes)
    actual = tuple(scene.source_scene_number for scene in response.scenes)
    if len(actual) != len(set(actual)):
        raise NarrationExpansionContractError(
            NarrationExpansionFailureCode.SCENE_DUPLICATE,
            metadata=metrics,
        )
    if set(actual) - set(expected):
        raise NarrationExpansionContractError(
            NarrationExpansionFailureCode.SCENE_UNKNOWN,
            metadata=metrics,
        )
    if set(expected) - set(actual) or len(actual) != len(expected):
        raise NarrationExpansionContractError(
            NarrationExpansionFailureCode.SCENE_MISSING,
            metadata=metrics,
        )
    response_by_source = {scene.source_scene_number: scene for scene in response.scenes}
    budget_by_source = {scene.source_scene_number: scene.maximum_words for scene in request.scenes}
    merged_scenes = []
    for source_scene in source_script.scenes:
        expanded = response_by_source[source_scene.source_scene_number]
        if narration_word_count(expanded.narration) > budget_by_source[
            source_scene.source_scene_number
        ]:
            raise NarrationExpansionContractError(
                NarrationExpansionFailureCode.SCENE_BUDGET_EXCEEDED,
                metadata=metrics,
            )
        merged_scenes.append(source_scene.model_copy(update={"narration": expanded.narration}))
    if sum(narration_word_count(scene.narration) for scene in response.scenes) > (
        request.maximum_total_words
    ):
        raise NarrationExpansionContractError(
            NarrationExpansionFailureCode.GLOBAL_BUDGET_EXCEEDED,
            metadata=metrics,
        )
    try:
        return ProductionScript.model_validate(
            source_script.model_copy(update={"scenes": tuple(merged_scenes)}).model_dump(
                mode="python"
            )
        )
    except ValidationError as exc:
        raise NarrationExpansionContractError(
            NarrationExpansionFailureCode.MERGE_INVALID,
            metadata=metrics,
        ) from exc


def parse_narration_expansion_response(
    payload: dict[str, object],
    *,
    request: NarrationExpansionRequest,
) -> NarrationExpansionResponse:
    """Validate raw decoded JSON with deterministic, safe leaf diagnostics."""

    base_metrics = narration_expansion_safe_metrics(request=request, payload=payload)
    if payload.get("schema_version") != "1.0.0":
        raise NarrationExpansionContractError(
            NarrationExpansionFailureCode.SCHEMA_INVALID,
            metadata=base_metrics,
        )
    language = payload.get("language")
    if not isinstance(language, str):
        raise NarrationExpansionContractError(
            NarrationExpansionFailureCode.SCHEMA_INVALID,
            metadata=base_metrics,
        )
    if language != request.language:
        raise NarrationExpansionContractError(
            NarrationExpansionFailureCode.LANGUAGE_MISMATCH,
            metadata=base_metrics,
        )
    scenes_value = payload.get("scenes")
    if not isinstance(scenes_value, list):
        raise NarrationExpansionContractError(
            NarrationExpansionFailureCode.SCHEMA_INVALID,
            metadata=base_metrics,
        )
    if len(scenes_value) > 50:
        raise NarrationExpansionContractError(
            NarrationExpansionFailureCode.SCHEMA_INVALID,
            metadata=base_metrics,
        )
    expected = tuple(scene.source_scene_number for scene in request.scenes)
    received: list[int] = []
    for scene in scenes_value:
        if not isinstance(scene, dict):
            raise NarrationExpansionContractError(
                NarrationExpansionFailureCode.SCHEMA_INVALID,
                metadata=base_metrics,
            )
        identity = scene.get("source_scene_number")
        if isinstance(identity, bool) or not isinstance(identity, int):
            raise NarrationExpansionContractError(
                NarrationExpansionFailureCode.SCHEMA_INVALID,
                metadata=base_metrics,
            )
        received.append(identity)
    if len(received) != len(set(received)):
        raise NarrationExpansionContractError(
            NarrationExpansionFailureCode.SCENE_DUPLICATE,
            metadata=base_metrics,
        )
    if set(received) - set(expected):
        raise NarrationExpansionContractError(
            NarrationExpansionFailureCode.SCENE_UNKNOWN,
            metadata=base_metrics,
        )
    if set(expected) - set(received) or len(received) != len(expected):
        raise NarrationExpansionContractError(
            NarrationExpansionFailureCode.SCENE_MISSING,
            metadata=base_metrics,
        )
    for scene in scenes_value:
        narration = scene.get("narration")
        if not isinstance(narration, str):
            raise NarrationExpansionContractError(
                NarrationExpansionFailureCode.SCHEMA_INVALID,
                metadata=base_metrics,
            )
        if not narration.strip():
            raise NarrationExpansionContractError(
                NarrationExpansionFailureCode.EMPTY_NARRATION,
                metadata=base_metrics,
            )
        try:
            validate_planning_text(narration)
        except ValueError as exc:
            raise NarrationExpansionContractError(
                NarrationExpansionFailureCode.UNSAFE_NARRATION,
                metadata=base_metrics,
            ) from exc
    try:
        return NarrationExpansionResponse.model_validate(payload)
    except ValidationError as exc:
        raise NarrationExpansionContractError(
            NarrationExpansionFailureCode.SCHEMA_INVALID,
            metadata=base_metrics,
        ) from exc


def narration_expansion_safe_metrics(
    *,
    request: NarrationExpansionRequest,
    response: NarrationExpansionResponse | None = None,
    payload: dict[str, object] | None = None,
) -> dict[str, bool | int | str]:
    """Return bounded semantic metrics without retaining any narration text."""

    expected_numbers = tuple(scene.source_scene_number for scene in request.scenes)
    budgets = tuple((scene.source_scene_number, scene.maximum_words) for scene in request.scenes)
    received_language = "missing"
    received: tuple[int, ...] = ()
    word_counts: tuple[tuple[int, int], ...] = ()
    if response is not None:
        received_language = _safe_language(response.language)
        received = tuple(scene.source_scene_number for scene in response.scenes)
        word_counts = tuple(
            (scene.source_scene_number, narration_word_count(scene.narration))
            for scene in response.scenes
        )
    elif payload is not None:
        received_language = _safe_language(payload.get("language"))
        scenes_value = payload.get("scenes")
        if isinstance(scenes_value, list):
            received_values: list[int] = []
            count_values: list[tuple[int, int]] = []
            for index, scene in enumerate(scenes_value[:50], start=1):
                if not isinstance(scene, dict):
                    continue
                identity = scene.get("source_scene_number")
                safe_identity = _safe_scene_identity(identity, fallback=-index)
                received_values.append(safe_identity)
                narration = scene.get("narration")
                if isinstance(narration, str):
                    count_values.append((safe_identity, narration_word_count(narration)))
            received = tuple(received_values)
            word_counts = tuple(count_values)
    return {
        "expected_language": _safe_language(request.language),
        "received_language": received_language,
        "expected_scene_numbers": _bounded_pairs(expected_numbers),
        "received_scene_numbers": _bounded_pairs(received),
        "expected_scene_count": len(expected_numbers),
        "received_scene_count": len(received),
        "scene_word_budgets": _bounded_word_pairs(budgets),
        "received_scene_word_counts": _bounded_word_pairs(word_counts),
        "global_word_budget": request.maximum_total_words,
        "received_total_word_count": sum(count for _, count in word_counts),
        "source_script_sha256": request.source_script_sha256,
    }


def _safe_language(value: Any) -> str:
    if not isinstance(value, str):
        return "missing"
    stripped = value.strip()
    if (
        not stripped
        or len(stripped) > 16
        or not stripped.isascii()
        or any(not character.isalnum() and character not in "-_" for character in stripped)
    ):
        return "invalid"
    return stripped


def _safe_scene_identity(value: Any, *, fallback: int) -> int:
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and -999 <= value <= 999
    ):
        return value
    return fallback


def _bounded_pairs(values: tuple[int, ...]) -> str:
    return ",".join(str(value) for value in values[:50]) or "none"


def _bounded_word_pairs(values: tuple[tuple[int, int], ...]) -> str:
    return ",".join(f"{identity}:{count}" for identity, count in values[:50]) or "none"


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
    "NarrationExpansionContractError",
    "NarrationExpansionFailureCode",
    "NarrationExpansionRequest",
    "NarrationExpansionResponse",
    "NarrationExpansionScene",
    "NarrationExpansionSourceScene",
    "merge_narration_expansion",
    "narration_expansion_safe_metrics",
    "narration_expansion_request",
    "parse_narration_expansion_response",
]
