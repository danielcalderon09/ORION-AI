"""Deterministic durable expansion from narrative scenes to final visual shots."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from pydantic import Field, model_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.duration_resolution import DurableDurationResolution
from backend.src.production.planning.provider_budget_planner import (
    ResolvedNarrativeScene,
    VisualShotAllocation,
    allocate_visual_shots,
)
from backend.src.production.planning.visual_strategy import LegacyFullVideoStrategy
from backend.src.production.scene_planning.models import (
    ProductionCamera,
    ProductionScene,
    ProductionScenePlan,
    ProductionShot,
    ProductionTiming,
    ProductionTransition,
)
from backend.src.production.scripting.models import NarrativeRole


class PostTtsShotExpansion(ContractModel):
    """Immutable post-TTS decision used by visual and video generation."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    job_id: UUID
    source_scene_plan_artifact_id: UUID
    source_scene_plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_duration_artifact_id: UUID
    source_duration_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    requested_target_duration_ms: int = Field(gt=0, le=3_600_000)
    resolved_duration_ms: int = Field(gt=0, le=3_600_000)
    supported_provider_durations_seconds: tuple[int, ...] = Field(min_length=1)
    allocations: tuple[VisualShotAllocation, ...] = Field(min_length=1, max_length=500)
    expanded_scene_plan: ProductionScenePlan
    plan_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    def calculated_fingerprint(self) -> str:
        payload = self.model_dump(mode="json", exclude={"plan_fingerprint"})
        content = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    @model_validator(mode="after")
    def validate_expansion(self) -> PostTtsShotExpansion:
        if self.plan_fingerprint != self.calculated_fingerprint():
            raise ValueError("shot expansion fingerprint differs")
        durations = self.supported_provider_durations_seconds
        if tuple(sorted(set(durations))) != durations or any(item <= 0 for item in durations):
            raise ValueError("shot expansion provider durations must be unique and ordered")
        shots = tuple(
            shot.shot_id
            for scene in self.expanded_scene_plan.scenes
            for shot in scene.shots
        )
        if shots != tuple(item.shot_id for item in self.allocations):
            raise ValueError("expanded scene shots differ from shot allocations")
        if round(self.expanded_scene_plan.target_duration_seconds * 1_000) != (
            self.resolved_duration_ms
        ):
            raise ValueError("expanded scene plan duration differs from resolved duration")
        return self


def build_post_tts_shot_expansion(
    *,
    job_id: UUID,
    source_scene_plan_artifact_id: UUID,
    source_scene_plan_sha256: str,
    source_duration_artifact_id: UUID,
    source_duration_sha256: str,
    scene_plan: ProductionScenePlan,
    duration_resolution: DurableDurationResolution,
    supported_provider_durations_seconds: tuple[int, ...],
) -> PostTtsShotExpansion:
    """Expand approved narrative scenes only after measured speech is accepted."""

    if not duration_resolution.accepted:
        raise ValueError("shot expansion requires accepted narration duration")
    by_scene = {item.scene_id: item for item in duration_resolution.scenes}
    if set(by_scene) != {scene.scene_id for scene in scene_plan.scenes}:
        raise ValueError("duration resolution scenes differ from narrative scene plan")
    supported = tuple(sorted(set(supported_provider_durations_seconds)))
    if not supported:
        raise ValueError("shot expansion requires provider durations")

    all_allocations: list[VisualShotAllocation] = []
    expanded_scenes: list[ProductionScene] = []
    for sequence_index, scene in enumerate(scene_plan.scenes):
        timing = by_scene[scene.scene_id]
        role = scene.story_beat.role if scene.story_beat is not None else NarrativeRole.DEVELOPMENT
        allocations = LegacyFullVideoStrategy().apply(
            allocate_visual_shots(
                ResolvedNarrativeScene(
                    scene_id=scene.scene_id,
                    sequence_index=sequence_index,
                    narrative_role=role,
                    editorial_target_ms=timing.planned_duration_ms,
                    actual_narration_ms=timing.actual_narration_duration_ms,
                    resolved_duration_ms=timing.resolved_duration_ms,
                ),
                supported_durations_seconds=supported,
            )
        )
        all_allocations.extend(allocations)
        expanded_scenes.append(_expanded_scene(scene, allocations))

    expanded = ProductionScenePlan(
        source_script_schema_version=scene_plan.source_script_schema_version,
        source_script_sha256=scene_plan.source_script_sha256,
        title=scene_plan.title,
        language=scene_plan.language,
        target_duration_seconds=duration_resolution.resolved_duration_ms / 1_000,
        scenes=tuple(expanded_scenes),
    )
    allocations = tuple(all_allocations)
    provisional = PostTtsShotExpansion.model_construct(
        plan_fingerprint="0" * 64,
        job_id=job_id,
        source_scene_plan_artifact_id=source_scene_plan_artifact_id,
        source_scene_plan_sha256=source_scene_plan_sha256,
        source_duration_artifact_id=source_duration_artifact_id,
        source_duration_sha256=source_duration_sha256,
        requested_target_duration_ms=duration_resolution.requested_target_duration_ms,
        resolved_duration_ms=duration_resolution.resolved_duration_ms,
        supported_provider_durations_seconds=supported,
        allocations=allocations,
        expanded_scene_plan=expanded,
    )
    return PostTtsShotExpansion(
        plan_fingerprint=provisional.calculated_fingerprint(),
        job_id=job_id,
        source_scene_plan_artifact_id=source_scene_plan_artifact_id,
        source_scene_plan_sha256=source_scene_plan_sha256,
        source_duration_artifact_id=source_duration_artifact_id,
        source_duration_sha256=source_duration_sha256,
        requested_target_duration_ms=duration_resolution.requested_target_duration_ms,
        resolved_duration_ms=duration_resolution.resolved_duration_ms,
        supported_provider_durations_seconds=supported,
        allocations=allocations,
        expanded_scene_plan=expanded,
    )


def _expanded_scene(
    source: ProductionScene,
    allocations: tuple[VisualShotAllocation, ...],
) -> ProductionScene:
    start_ms = 0
    shots: list[ProductionShot] = []
    original = source.shots[0]
    for index, allocation in enumerate(allocations):
        end_ms = start_ms + allocation.usable_duration_ms
        function = allocation.visual_function.value
        camera = _camera_for_function(original.camera, function)
        final = index == len(allocations) - 1
        shots.append(
            ProductionShot(
                shot_id=allocation.shot_id,
                shot_number=index + 1,
                scene_number=source.scene_number,
                objective=f"{source.objective}; {function} visual progression",
                description=(
                    f"{original.description} Shot {index + 1} of {len(allocations)}: "
                    f"{function} phase with distinct composition and temporal progression."
                ),
                camera=camera,
                timing=ProductionTiming(
                    start_seconds=start_ms / 1_000,
                    duration_seconds=allocation.usable_duration_ms / 1_000,
                    end_seconds=end_ms / 1_000,
                ),
                transition=(
                    original.transition
                    if final
                    else ProductionTransition(kind="cut", duration_seconds=0)
                ),
            )
        )
        start_ms = end_ms
    return source.model_copy(
        update={
            "estimated_duration_seconds": start_ms / 1_000,
            "shots": tuple(shots),
        }
    )


def _camera_for_function(camera: ProductionCamera, function: str) -> ProductionCamera:
    framing = {
        "establish": "wide",
        "advance": "medium",
        "reveal": "close_up",
        "resolve": "medium",
    }.get(function, camera.framing)
    movement = {
        "establish": "dolly",
        "advance": "pan",
        "reveal": "dolly",
        "resolve": "static",
    }.get(function, camera.movement)
    return camera.model_copy(update={"framing": framing, "movement": movement})


__all__ = ["PostTtsShotExpansion", "build_post_tts_shot_expansion"]
