"""Deterministic durable expansion from narrative scenes to final visual shots."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from math import ceil
from uuid import UUID

from pydantic import Field, model_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.duration_resolution import DurableDurationResolution
from backend.src.production.domain.visual_strategy import (
    VisualGenerationPriority,
    VisualImportance,
    VisualMode,
    VisualMotionMode,
)
from backend.src.production.planning.provider_budget_planner import (
    ResolvedNarrativeScene,
    VisualShotAllocation,
    VisualShotFunction,
    allocate_visual_shots,
)
from backend.src.production.planning.visual_strategy import (
    LegacyFullVideoStrategy,
    VisualStrategyName,
)
from backend.src.production.scene_planning.models import (
    ProductionCamera,
    ProductionScene,
    ProductionScenePlan,
    ProductionShot,
    ProductionTiming,
    ProductionTransition,
)
from backend.src.production.scripting.models import NarrativeRole


@dataclass(frozen=True, slots=True)
class VisualShotDensityPolicy:
    """Bounded image-shot density independent from narrative scene count."""

    target_visual_shot_duration_ms: int = 3_500
    minimum_visual_shots: int = 1
    maximum_visual_shots: int = 16

    def __post_init__(self) -> None:
        if self.target_visual_shot_duration_ms <= 0:
            raise ValueError("visual shot target duration must be positive")
        if self.minimum_visual_shots <= 0:
            raise ValueError("minimum visual shots must be positive")
        if self.maximum_visual_shots < self.minimum_visual_shots:
            raise ValueError("maximum visual shots must not be below minimum")

    def desired_shot_count(self, *, total_duration_ms: int, scene_count: int) -> int:
        """Return the bounded global target while retaining every source scene."""

        if total_duration_ms <= 0:
            raise ValueError("visual duration must be positive")
        if scene_count <= 0:
            raise ValueError("visual shot density requires narrative scenes")
        if scene_count > self.maximum_visual_shots:
            raise ValueError("narrative scene count exceeds maximum visual shots")
        duration_target = ceil(total_duration_ms / self.target_visual_shot_duration_ms)
        return min(
            self.maximum_visual_shots,
            max(self.minimum_visual_shots, scene_count, duration_target),
        )


DEFAULT_VISUAL_SHOT_DENSITY_POLICY = VisualShotDensityPolicy()


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
    visual_strategy_name: VisualStrategyName = VisualStrategyName.FULL_VIDEO,
    density_policy: VisualShotDensityPolicy = DEFAULT_VISUAL_SHOT_DENSITY_POLICY,
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

    shot_counts: tuple[int, ...] | None = None
    if visual_strategy_name is VisualStrategyName.IMAGE_ONLY:
        desired_shots = density_policy.desired_shot_count(
            total_duration_ms=duration_resolution.resolved_duration_ms,
            scene_count=len(scene_plan.scenes),
        )
        shot_counts = _distribute_image_shots(
            scene_durations_ms=tuple(
                by_scene[scene.scene_id].resolved_duration_ms
                for scene in scene_plan.scenes
            ),
            desired_shots=desired_shots,
        )

    all_allocations: list[VisualShotAllocation] = []
    expanded_scenes: list[ProductionScene] = []
    for sequence_index, scene in enumerate(scene_plan.scenes):
        timing = by_scene[scene.scene_id]
        role = scene.story_beat.role if scene.story_beat is not None else NarrativeRole.DEVELOPMENT
        resolved_scene = ResolvedNarrativeScene(
            scene_id=scene.scene_id,
            sequence_index=sequence_index,
            narrative_role=role,
            editorial_target_ms=timing.planned_duration_ms,
            actual_narration_ms=timing.actual_narration_duration_ms,
            resolved_duration_ms=timing.resolved_duration_ms,
        )
        if shot_counts is None:
            allocations = LegacyFullVideoStrategy().apply(
                allocate_visual_shots(
                    resolved_scene,
                    supported_durations_seconds=supported,
                )
            )
        else:
            allocations = _allocate_image_shots(
                resolved_scene,
                shot_count=shot_counts[sequence_index],
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


def _distribute_image_shots(
    *,
    scene_durations_ms: tuple[int, ...],
    desired_shots: int,
) -> tuple[int, ...]:
    """Allocate one shot per scene, then distribute extras by largest remainder."""

    if not scene_durations_ms or any(duration <= 0 for duration in scene_durations_ms):
        raise ValueError("scene durations must be positive")
    if desired_shots < len(scene_durations_ms):
        raise ValueError("visual shot target cannot represent every narrative scene")
    total_duration = sum(scene_durations_ms)
    remaining = desired_shots - len(scene_durations_ms)
    counts = [1] * len(scene_durations_ms)
    if remaining == 0:
        return tuple(counts)

    numerators = tuple(duration * remaining for duration in scene_durations_ms)
    extras = [numerator // total_duration for numerator in numerators]
    for index, extra in enumerate(extras):
        counts[index] += extra
    remainder = remaining - sum(extras)
    order = sorted(
        range(len(scene_durations_ms)),
        key=lambda index: (-(numerators[index] % total_duration), index),
    )
    for index in order[:remainder]:
        counts[index] += 1
    return tuple(counts)


def _allocate_image_shots(
    scene: ResolvedNarrativeScene,
    *,
    shot_count: int,
) -> tuple[VisualShotAllocation, ...]:
    """Split one scene exactly into provider-neutral generated-image slots."""

    if shot_count <= 0:
        raise ValueError("image shot count must be positive")
    duration, remainder = divmod(scene.resolved_duration_ms, shot_count)
    if duration <= 0:
        raise ValueError("image shot count exceeds scene duration in milliseconds")
    allocations: list[VisualShotAllocation] = []
    for index in range(shot_count):
        shot_number = index + 1
        usable_duration_ms = duration + (1 if index < remainder else 0)
        function = _visual_function(
            index=index,
            shot_count=shot_count,
            narrative_role=scene.narrative_role,
        )
        allocations.append(
            VisualShotAllocation(
                scene_id=scene.scene_id,
                shot_id=f"{scene.scene_id}-shot-{shot_number:03d}",
                shot_sequence_index=index,
                visual_asset_id=(
                    f"asset-s{scene.sequence_index + 1:03d}-q{shot_number:03d}-v001"
                ),
                narrative_role=scene.narrative_role,
                visual_function=function,
                intent_key=(
                    f"{scene.scene_id}:{scene.narrative_role.value}:{function.value}:"
                    f"{shot_number}-of-{shot_count}"
                ),
                usable_duration_ms=usable_duration_ms,
                visual_mode=VisualMode.GENERATED_IMAGE,
                motion_mode=VisualMotionMode.STATIC,
                importance=VisualImportance.MEDIUM,
                generation_priority=VisualGenerationPriority.NORMAL,
                provider_duration_seconds=None,
            )
        )
    return tuple(allocations)


def _visual_function(
    *,
    index: int,
    shot_count: int,
    narrative_role: NarrativeRole,
) -> VisualShotFunction:
    if shot_count == 1:
        return VisualShotFunction.PRIMARY
    if index == 0:
        return VisualShotFunction.ESTABLISH
    if index < shot_count - 1:
        return VisualShotFunction.ADVANCE
    if narrative_role in {
        NarrativeRole.REVEAL,
        NarrativeRole.PAYOFF,
        NarrativeRole.CONCLUSION,
    }:
        return VisualShotFunction.RESOLVE
    return VisualShotFunction.REVEAL


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


__all__ = [
    "DEFAULT_VISUAL_SHOT_DENSITY_POLICY",
    "PostTtsShotExpansion",
    "VisualShotDensityPolicy",
    "build_post_tts_shot_expansion",
]
