"""Provider-neutral contracts for one bounded narration-compression pass."""

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


class NarrationCompressionFailureCode(StrEnum):
    """Stable, non-sensitive reasons for rejecting compression output."""

    SCHEMA_INVALID = "narration_compression_schema_invalid"
    SCENE_MISSING = "narration_compression_scene_missing"
    SCENE_DUPLICATE = "narration_compression_scene_duplicate"
    SCENE_UNKNOWN = "narration_compression_scene_unknown"
    EMPTY_NARRATION = "narration_compression_empty_narration"
    UNSAFE_NARRATION = "narration_compression_unsafe_narration"
    SCENE_BELOW_MINIMUM = "narration_compression_scene_below_minimum_word_budget"
    SCENE_ABOVE_MAXIMUM = "narration_compression_scene_above_maximum_word_budget"
    GLOBAL_BELOW_MINIMUM = "narration_compression_below_minimum_word_budget"
    GLOBAL_ABOVE_MAXIMUM = "narration_compression_above_maximum_word_budget"
    SOURCE_MISMATCH = "narration_compression_source_mismatch"
    MERGE_INVALID = "narration_compression_merge_invalid"


_FAILURE_MESSAGES: dict[NarrationCompressionFailureCode, str] = {
    NarrationCompressionFailureCode.SCHEMA_INVALID: "narration compression schema is invalid",
    NarrationCompressionFailureCode.SCENE_MISSING: "narration compression is missing a source scene",
    NarrationCompressionFailureCode.SCENE_DUPLICATE: (
        "narration compression contains a duplicate source scene"
    ),
    NarrationCompressionFailureCode.SCENE_UNKNOWN: (
        "narration compression contains an unknown source scene"
    ),
    NarrationCompressionFailureCode.EMPTY_NARRATION: (
        "narration compression contains empty narration"
    ),
    NarrationCompressionFailureCode.UNSAFE_NARRATION: (
        "narration compression contains unsafe narration"
    ),
    NarrationCompressionFailureCode.SCENE_BELOW_MINIMUM: (
        "narration compression is below a scene word budget"
    ),
    NarrationCompressionFailureCode.SCENE_ABOVE_MAXIMUM: (
        "narration compression exceeds a scene word budget"
    ),
    NarrationCompressionFailureCode.GLOBAL_BELOW_MINIMUM: (
        "narration compression is below the global word budget"
    ),
    NarrationCompressionFailureCode.GLOBAL_ABOVE_MAXIMUM: (
        "narration compression exceeds the global word budget"
    ),
    NarrationCompressionFailureCode.SOURCE_MISMATCH: (
        "narration compression source binding differs"
    ),
    NarrationCompressionFailureCode.MERGE_INVALID: (
        "narration compression could not produce a valid script"
    ),
}


class NarrationCompressionContractError(ValueError):
    """A classified compression rejection containing safe metrics only."""

    def __init__(
        self,
        code: NarrationCompressionFailureCode,
        *,
        metadata: dict[str, bool | int | str],
    ) -> None:
        super().__init__(_FAILURE_MESSAGES[code])
        self.code = code
        self.safe_message = _FAILURE_MESSAGES[code]
        self.safe_metadata = metadata


class NarrationCompressionSourceScene(ContractModel):
    source_scene_number: int = Field(ge=1, le=50)
    original_narration: str = Field(min_length=1, max_length=6000)
    minimum_words: int = Field(ge=2, le=1000)
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
    source_script_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    language: str = Field(min_length=2, max_length=16)
    target_duration_ms: int = Field(gt=0, le=60_000)
    minimum_duration_ms: int = Field(gt=0, le=60_000)
    ideal_duration_ms: int = Field(gt=0, le=60_000)
    maximum_duration_ms: int = Field(gt=0, le=60_000)
    reading_speed_words_per_minute: int = Field(ge=80, le=240)
    minimum_total_words: int = Field(ge=2, le=1000)
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
        if sum(scene.minimum_words for scene in self.scenes) != self.minimum_total_words:
            raise ValueError("compression scene minimums must equal the total minimum")
        if sum(scene.maximum_words for scene in self.scenes) != self.maximum_total_words:
            raise ValueError("compression scene budgets must equal the total budget")
        if self.minimum_total_words > self.maximum_total_words:
            raise ValueError("compression total word band is invalid")
        if any(scene.minimum_words > scene.maximum_words for scene in self.scenes):
            raise ValueError("compression scene word band is invalid")
        if not (
            self.minimum_duration_ms
            <= self.ideal_duration_ms
            <= self.maximum_duration_ms
            <= self.target_duration_ms
        ):
            raise ValueError("compression duration window is invalid")
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
    minimum_duration_ms: int,
    ideal_duration_ms: int,
    maximum_duration_ms: int,
    minimum_total_words: int,
    maximum_total_words: int,
    scene_minimum_word_budgets: tuple[int, ...],
    scene_maximum_word_budgets: tuple[int, ...],
) -> NarrationCompressionRequest:
    """Build the narrow request while retaining only semantic compression inputs."""

    if len(scene_minimum_word_budgets) != len(source_script.scenes) or len(
        scene_maximum_word_budgets
    ) != len(source_script.scenes):
        raise ValueError("compression scene budget count differs")
    source_scenes = tuple(
        NarrationCompressionSourceScene(
            source_scene_number=scene.source_scene_number,
            original_narration=scene.narration,
            minimum_words=minimum_words,
            maximum_words=maximum_words,
            semantic_constraints=_scene_semantic_constraints(source_script, index),
        )
        for index, (scene, minimum_words, maximum_words) in enumerate(
            zip(
                source_script.scenes,
                scene_minimum_word_budgets,
                scene_maximum_word_budgets,
                strict=True,
            )
        )
    )
    return NarrationCompressionRequest(
        job_id=job_id,
        source_script_sha256=hashlib.sha256(
            serialize_production_script(source_script)
        ).hexdigest(),
        language=source_script.language,
        target_duration_ms=assessment.target_duration_ms,
        minimum_duration_ms=minimum_duration_ms,
        ideal_duration_ms=ideal_duration_ms,
        maximum_duration_ms=maximum_duration_ms,
        reading_speed_words_per_minute=assessment.reading_speed_words_per_minute,
        minimum_total_words=minimum_total_words,
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
    """Replace narration only after source, identity, and word-band validation."""

    metrics = narration_compression_safe_metrics(request=request, response=response)
    actual_source_hash = hashlib.sha256(
        serialize_production_script(source_script)
    ).hexdigest()
    if actual_source_hash != request.source_script_sha256:
        raise NarrationCompressionContractError(
            NarrationCompressionFailureCode.SOURCE_MISMATCH,
            metadata=metrics,
        )
    expected = tuple(scene.source_scene_number for scene in request.scenes)
    actual = tuple(scene.source_scene_number for scene in response.scenes)
    if len(actual) != len(set(actual)):
        raise NarrationCompressionContractError(
            NarrationCompressionFailureCode.SCENE_DUPLICATE,
            metadata=metrics,
        )
    if set(actual) - set(expected):
        raise NarrationCompressionContractError(
            NarrationCompressionFailureCode.SCENE_UNKNOWN,
            metadata=metrics,
        )
    if set(expected) - set(actual) or len(actual) != len(expected):
        raise NarrationCompressionContractError(
            NarrationCompressionFailureCode.SCENE_MISSING,
            metadata=metrics,
        )
    total_words = sum(narration_word_count(scene.narration) for scene in response.scenes)
    if total_words < request.minimum_total_words:
        raise NarrationCompressionContractError(
            NarrationCompressionFailureCode.GLOBAL_BELOW_MINIMUM,
            metadata=metrics,
        )
    if total_words > request.maximum_total_words:
        raise NarrationCompressionContractError(
            NarrationCompressionFailureCode.GLOBAL_ABOVE_MAXIMUM,
            metadata=metrics,
        )
    response_by_source = {scene.source_scene_number: scene for scene in response.scenes}
    bands_by_source = {
        scene.source_scene_number: (scene.minimum_words, scene.maximum_words)
        for scene in request.scenes
    }
    merged_scenes = []
    for source_scene in source_script.scenes:
        compressed = response_by_source[source_scene.source_scene_number]
        count = narration_word_count(compressed.narration)
        minimum_words, maximum_words = bands_by_source[source_scene.source_scene_number]
        if count < minimum_words:
            raise NarrationCompressionContractError(
                NarrationCompressionFailureCode.SCENE_BELOW_MINIMUM,
                metadata=metrics,
            )
        if count > maximum_words:
            raise NarrationCompressionContractError(
                NarrationCompressionFailureCode.SCENE_ABOVE_MAXIMUM,
                metadata=metrics,
            )
        merged_scenes.append(source_scene.model_copy(update={"narration": compressed.narration}))
    try:
        return ProductionScript.model_validate(
            source_script.model_copy(update={"scenes": tuple(merged_scenes)}).model_dump(
                mode="python"
            )
        )
    except ValidationError as exc:
        raise NarrationCompressionContractError(
            NarrationCompressionFailureCode.MERGE_INVALID,
            metadata=metrics,
        ) from exc


def parse_narration_compression_response(
    payload: dict[str, object],
    *,
    request: NarrationCompressionRequest,
) -> NarrationCompressionResponse:
    """Validate decoded JSON with stable, safe compression diagnostics."""

    metrics = narration_compression_safe_metrics(request=request, payload=payload)
    if payload.get("schema_version") != "1.0.0":
        raise NarrationCompressionContractError(
            NarrationCompressionFailureCode.SCHEMA_INVALID,
            metadata=metrics,
        )
    scenes_value = payload.get("scenes")
    if not isinstance(scenes_value, list) or len(scenes_value) > 50:
        raise NarrationCompressionContractError(
            NarrationCompressionFailureCode.SCHEMA_INVALID,
            metadata=metrics,
        )
    expected = tuple(scene.source_scene_number for scene in request.scenes)
    received: list[int] = []
    for scene in scenes_value:
        if not isinstance(scene, dict):
            raise NarrationCompressionContractError(
                NarrationCompressionFailureCode.SCHEMA_INVALID,
                metadata=metrics,
            )
        identity = scene.get("source_scene_number")
        if isinstance(identity, bool) or not isinstance(identity, int):
            raise NarrationCompressionContractError(
                NarrationCompressionFailureCode.SCHEMA_INVALID,
                metadata=metrics,
            )
        received.append(identity)
    if len(received) != len(set(received)):
        raise NarrationCompressionContractError(
            NarrationCompressionFailureCode.SCENE_DUPLICATE,
            metadata=metrics,
        )
    if set(received) - set(expected):
        raise NarrationCompressionContractError(
            NarrationCompressionFailureCode.SCENE_UNKNOWN,
            metadata=metrics,
        )
    if set(expected) - set(received) or len(received) != len(expected):
        raise NarrationCompressionContractError(
            NarrationCompressionFailureCode.SCENE_MISSING,
            metadata=metrics,
        )
    for scene in scenes_value:
        narration = scene.get("narration")
        if not isinstance(narration, str):
            raise NarrationCompressionContractError(
                NarrationCompressionFailureCode.SCHEMA_INVALID,
                metadata=metrics,
            )
        if not narration.strip():
            raise NarrationCompressionContractError(
                NarrationCompressionFailureCode.EMPTY_NARRATION,
                metadata=metrics,
            )
        try:
            validate_planning_text(narration)
        except ValueError as exc:
            raise NarrationCompressionContractError(
                NarrationCompressionFailureCode.UNSAFE_NARRATION,
                metadata=metrics,
            ) from exc
    try:
        return NarrationCompressionResponse.model_validate(payload)
    except ValidationError as exc:
        raise NarrationCompressionContractError(
            NarrationCompressionFailureCode.SCHEMA_INVALID,
            metadata=metrics,
        ) from exc


def narration_compression_safe_metrics(
    *,
    request: NarrationCompressionRequest,
    response: NarrationCompressionResponse | None = None,
    payload: dict[str, object] | None = None,
) -> dict[str, bool | int | str]:
    """Return bounded word-band metrics without retaining narration text."""

    expected_numbers = tuple(scene.source_scene_number for scene in request.scenes)
    bands = tuple(
        (scene.source_scene_number, scene.minimum_words, scene.maximum_words)
        for scene in request.scenes
    )
    received: tuple[int, ...] = ()
    word_counts: tuple[tuple[int, int], ...] = ()
    if response is not None:
        received = tuple(scene.source_scene_number for scene in response.scenes)
        word_counts = tuple(
            (scene.source_scene_number, narration_word_count(scene.narration))
            for scene in response.scenes
        )
    elif payload is not None:
        received_values: list[int] = []
        count_values: list[tuple[int, int]] = []
        scenes_value = payload.get("scenes")
        if isinstance(scenes_value, list):
            for index, scene in enumerate(scenes_value[:50], start=1):
                if not isinstance(scene, dict):
                    continue
                identity = _safe_scene_identity(
                    scene.get("source_scene_number"), fallback=-index
                )
                received_values.append(identity)
                narration = scene.get("narration")
                if isinstance(narration, str):
                    count_values.append((identity, narration_word_count(narration)))
        received = tuple(received_values)
        word_counts = tuple(count_values)
    return {
        "expected_scene_numbers": _bounded_numbers(expected_numbers),
        "received_scene_numbers": _bounded_numbers(received),
        "expected_scene_count": len(expected_numbers),
        "received_scene_count": len(received),
        "scene_word_bands": _bounded_bands(bands),
        "received_scene_word_counts": _bounded_word_counts(word_counts),
        "minimum_total_words": request.minimum_total_words,
        "maximum_total_words": request.maximum_total_words,
        "received_total_word_count": sum(count for _, count in word_counts),
        "minimum_duration_ms": request.minimum_duration_ms,
        "ideal_duration_ms": request.ideal_duration_ms,
        "maximum_duration_ms": request.maximum_duration_ms,
        "source_script_sha256": request.source_script_sha256,
    }


def _safe_scene_identity(value: Any, *, fallback: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and -999 <= value <= 999:
        return value
    return fallback


def _bounded_numbers(values: tuple[int, ...]) -> str:
    return ",".join(str(value) for value in values[:50]) or "none"


def _bounded_word_counts(values: tuple[tuple[int, int], ...]) -> str:
    return ",".join(f"{identity}:{count}" for identity, count in values[:50]) or "none"


def _bounded_bands(values: tuple[tuple[int, int, int], ...]) -> str:
    return ",".join(
        f"{identity}:{minimum}-{maximum}"
        for identity, minimum, maximum in values[:50]
    ) or "none"


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
    "NarrationCompressionContractError",
    "NarrationCompressionFailureCode",
    "NarrationCompressionRequest",
    "NarrationCompressionResponse",
    "NarrationCompressionScene",
    "NarrationCompressionSourceScene",
    "merge_narration_compression",
    "narration_compression_safe_metrics",
    "narration_compression_request",
    "parse_narration_compression_response",
]
