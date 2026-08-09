"""Offline behavioral tests for adaptive narrative progression."""

from __future__ import annotations

import pytest

from backend.src.production.application.orchestration.stage_registry import StageRegistry
from backend.src.production.domain.enums import ProductionStage
from backend.src.production.scene_planning.models import ProductionScenePlan
from backend.src.production.scene_planning.providers.simulated_provider import (
    SimulatedScenePlanningProvider,
)
from backend.src.production.scripting.models import (
    NarrativeRole,
    ProductionScript,
    ProductionScriptScene,
    adaptive_narrative_roles,
    ensure_narrative_progression,
    validate_narration_repetition,
)
from backend.src.production.scripting.serialization import serialize_production_script
from backend.src.production.speech_generation.configuration import SpeechGenerationConfiguration
from backend.src.production.speech_generation.ports import ReadSpeechSourceScript
from backend.src.production.speech_generation.segment_builder import build_speech_segments
from backend.src.production.visual_asset_planning.models import VideoIdentity


def make_script(scene_count: int) -> ProductionScript:
    return ProductionScript(
        source_plan_schema_version="1.0.0",
        title="Three discoveries beneath the ocean",
        language="es-ES",
        target_duration_seconds=float(scene_count),
        tone="documentary",
        opening_hook="El océano todavía guarda secretos.",
        scenes=tuple(
            ProductionScriptScene(
                scene_number=index,
                source_scene_number=index,
                heading=f"Discovery {index}",
                narration=f"Cada escena aporta el dato nuevo número {index}.",
                estimated_duration_seconds=1,
                delivery_style="calm",
                visual_intent=f"Different evidence {index}",
            )
            for index in range(1, scene_count + 1)
        ),
    )


def test_two_scene_arc_is_hook_then_payoff():
    script = ensure_narrative_progression(make_script(2))
    assert [scene.story_beat.role for scene in script.scenes] == [
        NarrativeRole.HOOK,
        NarrativeRole.PAYOFF,
    ]
    assert script.scenes[1].story_beat.open_question is None


def test_five_scene_arc_progresses_without_requiring_all_roles():
    script = ensure_narrative_progression(make_script(5))
    assert [scene.story_beat.role for scene in script.scenes] == [
        NarrativeRole.HOOK,
        NarrativeRole.SETUP,
        NarrativeRole.ESCALATION,
        NarrativeRole.REVEAL,
        NarrativeRole.PAYOFF,
    ]
    new_information = tuple(scene.story_beat.new_information for scene in script.scenes)
    assert all(new_information)
    assert len(set(new_information)) == 5


def test_long_form_uses_repeated_development_beats_adaptively():
    roles = adaptive_narrative_roles(8)
    assert roles[0] is NarrativeRole.HOOK
    assert roles[-3:] == (
        NarrativeRole.ESCALATION,
        NarrativeRole.REVEAL,
        NarrativeRole.PAYOFF,
    )
    assert roles.count(NarrativeRole.DEVELOPMENT) == 3


def test_hook_and_payoff_guards_reject_invalid_positions():
    script = ensure_narrative_progression(make_script(3))
    invalid = script.scenes[1].story_beat.model_copy(update={"role": NarrativeRole.PAYOFF})
    bad = script.model_copy(
        update={"scenes": (script.scenes[0], script.scenes[1].model_copy(update={"story_beat": invalid}), script.scenes[2])}
    )
    with pytest.raises(ValueError, match="payoff"):
        ProductionScript.model_validate(bad.model_dump(mode="json"))


def test_repetition_guard_rejects_identical_consecutive_narration():
    script = ensure_narrative_progression(make_script(2))
    repeated = script.scenes[1].model_copy(update={"narration": script.scenes[0].narration})
    bad = script.model_copy(update={"scenes": (script.scenes[0], repeated)})
    with pytest.raises(ValueError, match="repeat"):
        validate_narration_repetition(bad)


def test_global_arc_is_stable_when_only_scene_intent_changes():
    script = ensure_narrative_progression(make_script(3))
    changed_beat = script.scenes[1].story_beat.model_copy(
        update={"new_information": "A different clue"}
    )
    changed_scene = script.scenes[1].model_copy(
        update={"visual_intent": "A different clue", "story_beat": changed_beat}
    )
    changed = script.model_copy(update={"scenes": (script.scenes[0], changed_scene, script.scenes[2])})
    assert script.narrative_arc == changed.narrative_arc
    assert script.scenes[1].story_beat.new_information != changed.scenes[1].story_beat.new_information


def test_narrative_serialization_is_deterministic_and_historical_shape_loads():
    script = ensure_narrative_progression(make_script(2))
    assert serialize_production_script(script) == serialize_production_script(script)
    historical = script.model_dump(mode="json", exclude={"narrative_arc"})
    historical["scenes"] = [
        {key: value for key, value in scene.items() if key != "story_beat"}
        for scene in historical["scenes"]
    ]
    loaded = ProductionScript.model_validate(historical)
    assert loaded.narrative_arc is None
    assert all(scene.story_beat is None for scene in loaded.scenes)


@pytest.mark.asyncio
async def test_scene_planning_preserves_story_beats():
    script = ensure_narrative_progression(make_script(2))
    response = await SimulatedScenePlanningProvider().generate_scene_plan(script)
    plan = ProductionScenePlan.model_validate(response.scene_plan.model_dump(mode="json"))
    assert [scene.story_beat.role for scene in plan.scenes] == [
        NarrativeRole.HOOK,
        NarrativeRole.PAYOFF,
    ]


def test_speech_segments_carry_narrative_context():
    script = ensure_narrative_progression(make_script(2))
    source = ReadSpeechSourceScript(
        script=script,
        artifact_id="30000000-0000-4000-8000-000000000701",
        relative_path="production/job/scripting/script.json",
        sha256="a" * 64,
        size_bytes=100,
        schema_version="1.0.0",
    )
    segments = build_speech_segments(source, SpeechGenerationConfiguration())
    assert segments[0].narrative_arc == script.narrative_arc
    assert segments[1].story_beat == script.scenes[1].story_beat


def test_narrative_arc_is_typed_and_independent_from_video_identity():
    script = ensure_narrative_progression(make_script(2))
    identity = VideoIdentity(visual_style="cinematic documentary")
    assert script.narrative_arc is not identity
    assert script.narrative_arc.premise != identity.visual_style


def test_audio_first_and_single_pass_video_order_remain_intact():
    stages = StageRegistry.active_stages(generate_clips_after_render=False)
    assert stages.index(ProductionStage.GENERATING_NARRATION) < stages.index(
        ProductionStage.GENERATING_VIDEO_CLIPS
    )
    assert ProductionStage.GENERATING_VIDEO_CLIPS in stages
