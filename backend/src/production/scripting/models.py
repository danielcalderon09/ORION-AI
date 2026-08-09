"""Strict, versioned ProductionScript contracts."""

import math
from enum import StrEnum
from typing import Any, cast

from pydantic import Field, field_validator, model_validator

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.domain.base import ContractModel
from backend.src.production.planning.models import ProductionPlan
from backend.src.production.planning.validation import validate_planning_text


class NarrativeRole(StrEnum):
    HOOK = "hook"
    SETUP = "setup"
    DEVELOPMENT = "development"
    ESCALATION = "escalation"
    REVEAL = "reveal"
    PAYOFF = "payoff"
    CONCLUSION = "conclusion"


class NarrativeArc(ContractModel):
    """Global story context; independent from visual identity and scene details."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    premise: str = Field(min_length=1, max_length=1500)
    opening_hook: str = Field(min_length=1, max_length=1000)
    central_question: str = Field(min_length=1, max_length=1000)
    progression: tuple[str, ...] = Field(min_length=1, max_length=50)
    intended_payoff: str = Field(min_length=1, max_length=1000)
    ending_state: str = Field(min_length=1, max_length=1000)

    @field_validator(
        "premise",
        "opening_hook",
        "central_question",
        "intended_payoff",
        "ending_state",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        return validate_planning_text(value)

    @field_validator("progression")
    @classmethod
    def validate_progression(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(validate_planning_text(item) for item in value)


class StoryBeat(ContractModel):
    """Minimal scene-level narrative intent derived from the global arc."""

    role: NarrativeRole
    information_introduced: str = Field(min_length=1, max_length=1200)
    prior_context: str = Field(min_length=1, max_length=1200)
    new_information: str = Field(min_length=1, max_length=1500)
    open_question: str | None = Field(default=None, max_length=1000)
    transition_intent: str = Field(min_length=1, max_length=1000)
    avoid_repetition: tuple[str, ...] = Field(default=(), max_length=20)

    @field_validator(
        "information_introduced",
        "prior_context",
        "new_information",
        "open_question",
        "transition_intent",
    )
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        return None if value is None else validate_planning_text(value)

    @field_validator("avoid_repetition")
    @classmethod
    def validate_repetition_rules(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(validate_planning_text(item) for item in value)


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
    story_beat: StoryBeat | None = None
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
    narrative_arc: NarrativeArc | None = None
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
        _validate_narrative_progression(self)
        return self


def adaptive_narrative_roles(scene_count: int) -> tuple[NarrativeRole, ...]:
    """Select a progression shape without imposing a fixed scene count."""

    if scene_count < 1:
        raise ValueError("scene count must be positive")
    if scene_count == 1:
        return (NarrativeRole.HOOK,)
    if scene_count == 2:
        return (NarrativeRole.HOOK, NarrativeRole.PAYOFF)
    if scene_count == 3:
        return (NarrativeRole.HOOK, NarrativeRole.DEVELOPMENT, NarrativeRole.PAYOFF)
    if scene_count == 4:
        return (
            NarrativeRole.HOOK,
            NarrativeRole.SETUP,
            NarrativeRole.ESCALATION,
            NarrativeRole.PAYOFF,
        )
    return (
        NarrativeRole.HOOK,
        NarrativeRole.SETUP,
        *(NarrativeRole.DEVELOPMENT for _ in range(scene_count - 5)),
        NarrativeRole.ESCALATION,
        NarrativeRole.REVEAL,
        NarrativeRole.PAYOFF,
    )


def derive_narrative_arc(script: ProductionScript) -> NarrativeArc:
    """Create stable global context without embedding scene-specific text."""

    return NarrativeArc(
        premise=f"A coherent video about {script.title}.",
        opening_hook=script.opening_hook,
        central_question=f"What will the viewer understand about {script.title} by the end?",
        progression=(
            "Introduce the premise, develop distinct evidence, and resolve the central question.",
        ),
        intended_payoff="Resolve the central question in the final scene.",
        ending_state=script.closing_call_to_action or "The final scene leaves the story resolved.",
    )


def derive_story_beats(script: ProductionScript) -> tuple[StoryBeat, ...]:
    """Derive deterministic scene intents while preserving scene-specific content."""

    roles = adaptive_narrative_roles(len(script.scenes))
    beats: list[StoryBeat] = []
    for index, (scene, role) in enumerate(zip(script.scenes, roles, strict=True)):
        is_first = index == 0
        is_last = index == len(script.scenes) - 1
        beats.append(
            StoryBeat(
                role=role,
                information_introduced=scene.heading,
                prior_context=(
                    "No prior scene; establish the premise."
                    if is_first
                    else "Build on information already established without repeating it."
                ),
                new_information=scene.visual_intent,
                open_question=(
                    None
                    if is_last
                    else "What new evidence or consequence will the next scene reveal?"
                ),
                transition_intent=(
                    "Open the story with the approved hook."
                    if is_first
                    else (
                        "Resolve the central question and provide the intended payoff."
                        if is_last
                        else "Increase understanding or tension toward the next scene."
                    )
                ),
                avoid_repetition=(
                    ()
                    if is_first
                    else ("Do not repeat the opening hook or previously stated facts.",)
                ),
            )
        )
    return tuple(beats)


def ensure_narrative_progression(script: ProductionScript) -> ProductionScript:
    """Fill narrative fields for new outputs while preserving explicit provider work."""

    arc = script.narrative_arc or derive_narrative_arc(script)
    derived = derive_story_beats(script)
    scenes = tuple(
        scene.model_copy(
            update={"story_beat": scene.story_beat or beat}
        )
        for scene, beat in zip(script.scenes, derived, strict=True)
    )
    return script.model_copy(update={"narrative_arc": arc, "scenes": scenes})


def _validate_narrative_progression(script: ProductionScript) -> None:
    if script.narrative_arc is None or any(scene.story_beat is None for scene in script.scenes):
        return
    beats = tuple(scene.story_beat for scene in script.scenes)
    if any(beat is None for beat in beats):
        return
    resolved_beats = tuple(cast(StoryBeat, beat) for beat in beats)
    if resolved_beats[0].role is not NarrativeRole.HOOK:
        raise ValueError("the first scene must be the narrative hook")
    if any(beat.role is NarrativeRole.HOOK for beat in resolved_beats[1:]):
        raise ValueError("the narrative hook may appear only in the first scene")
    if any(
        beat.role in {NarrativeRole.PAYOFF, NarrativeRole.CONCLUSION}
        for beat in resolved_beats[:-1]
    ):
        raise ValueError("payoff or conclusion cannot occur before the final scene")


def validate_narration_repetition(script: ProductionScript) -> None:
    """Reject exact consecutive narration reuse after duration checks run."""

    if script.narrative_arc is None or any(scene.story_beat is None for scene in script.scenes):
        return
    normalized = tuple(" ".join(scene.narration.casefold().split()) for scene in script.scenes)
    if any(left == right for left, right in zip(normalized, normalized[1:], strict=False)):
        raise ValueError("consecutive scenes must not repeat the same narration")


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
