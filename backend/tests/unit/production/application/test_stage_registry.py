"""Unit tests for deterministic production stage ordering."""

from backend.src.production.application.orchestration import StageRegistry
from backend.src.production.domain.enums import ProductionStage


def test_stage_registry_returns_next_and_previous_stage() -> None:
    assert StageRegistry.next_stage(
        ProductionStage.PLANNING,
        generate_clips_after_render=False,
    ) is ProductionStage.SCRIPTING
    assert StageRegistry.previous_stage(
        ProductionStage.SCRIPTING,
        generate_clips_after_render=False,
    ) is ProductionStage.PLANNING


def test_stage_registry_omits_clip_handoff_when_disabled() -> None:
    stages = StageRegistry.active_stages(generate_clips_after_render=False)

    assert ProductionStage.HANDING_OFF_TO_CLIPS not in stages
    assert ProductionStage.WAITING_FOR_CLIPS not in stages
    assert StageRegistry.next_stage(
        ProductionStage.VALIDATING_RENDER,
        generate_clips_after_render=False,
    ) is ProductionStage.COMPLETED


def test_stage_registry_includes_clip_handoff_when_enabled() -> None:
    stages = StageRegistry.active_stages(generate_clips_after_render=True)

    assert ProductionStage.HANDING_OFF_TO_CLIPS in stages
    assert ProductionStage.WAITING_FOR_CLIPS in stages
    assert StageRegistry.next_stage(
        ProductionStage.VALIDATING_RENDER,
        generate_clips_after_render=True,
    ) is ProductionStage.HANDING_OFF_TO_CLIPS


def test_stage_registry_identifies_terminal_and_membership() -> None:
    assert StageRegistry.is_terminal(ProductionStage.COMPLETED)
    assert not StageRegistry.is_terminal(ProductionStage.PLANNING)
    assert all(StageRegistry.belongs_to_pipeline(stage) for stage in ProductionStage)
