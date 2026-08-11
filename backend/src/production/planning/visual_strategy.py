"""Pure, durable visual strategy planning with no provider side effects."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from math import ceil
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.visual_strategy import (
    VisualGenerationPriority,
    VisualImportance,
    VisualMode,
    VisualMotionMode,
)
from backend.src.production.planning.provider_budget_planner import (
    VisualShotAllocation,
    VisualShotFunction,
)
from backend.src.production.scripting.models import NarrativeRole


class VisualStrategyName(StrEnum):
    FULL_VIDEO = "full_video"
    HYBRID_BALANCED = "hybrid_balanced"
    HYBRID_ECONOMY = "hybrid_economy"


class VisualStrategyQualityError(ValueError):
    """Raised when an explicit strategy policy cannot meet its quality floor."""


class HybridVisualStrategyPolicy(ContractModel):
    maximum_generated_video_shots: int | None = Field(default=None, ge=0, le=500)
    allow_quality_degradation: bool = False


class StrategicVisualShot(ContractModel):
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    scene_sequence_index: int = Field(ge=0, le=49)
    shot_id: str = Field(pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$")
    shot_sequence_index: int = Field(ge=0, le=99)
    visual_asset_id: str = Field(pattern=r"^asset-s[0-9]{3}-q[0-9]{3}-v[0-9]{3}$")
    narrative_role: NarrativeRole
    visual_function: VisualShotFunction
    intent_key: str = Field(min_length=1, max_length=300)
    usable_duration_ms: int = Field(gt=0, le=600_000)
    visual_mode: VisualMode
    motion_mode: VisualMotionMode
    source_asset_id: str | None = Field(
        default=None,
        min_length=3,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    importance: VisualImportance
    generation_priority: VisualGenerationPriority
    provider_duration_seconds: int | None = Field(default=None, gt=0, le=600)

    @model_validator(mode="after")
    def validate_strategy(self) -> StrategicVisualShot:
        if self.scene_sequence_index != _scene_sequence_index(self.scene_id):
            raise ValueError("strategic shot scene sequence differs from scene ID")
        reused = self.visual_mode in {VisualMode.REUSED_IMAGE, VisualMode.REUSED_VIDEO}
        if reused != (self.source_asset_id is not None):
            raise ValueError("only reused visual shots require source_asset_id")
        if self.visual_mode is VisualMode.GENERATED_VIDEO:
            if self.provider_duration_seconds is None:
                raise ValueError("generated video requires provider duration")
            if self.provider_duration_seconds * 1_000 < self.usable_duration_ms:
                raise ValueError("generated video purchase undercovers its visual shot")
        elif self.provider_duration_seconds is not None:
            raise ValueError("non-generated video cannot carry provider purchase duration")
        if (
            self.motion_mode is not VisualMotionMode.STATIC
            and self.visual_mode not in {VisualMode.GENERATED_IMAGE, VisualMode.REUSED_IMAGE}
        ):
            raise ValueError("local motion requires an image visual mode")
        return self


class HybridVisualStrategySummary(ContractModel):
    visual_shot_count: int = Field(gt=0, le=500)
    generated_video_shots: int = Field(ge=0, le=500)
    generated_image_shots: int = Field(ge=0, le=500)
    reused_video_shots: int = Field(ge=0, le=500)
    reused_image_shots: int = Field(ge=0, le=500)
    quality_floor_pass: bool
    quality_degradation_authorized: bool
    maximum_consecutive_image_shots: int = Field(ge=0, le=500)

    @model_validator(mode="after")
    def validate_counts(self) -> HybridVisualStrategySummary:
        if self.visual_shot_count != (
            self.generated_video_shots
            + self.generated_image_shots
            + self.reused_video_shots
            + self.reused_image_shots
        ):
            raise ValueError("visual strategy summary counts differ")
        if self.quality_degradation_authorized and self.quality_floor_pass:
            raise ValueError("quality degradation cannot be authorized when the floor passes")
        return self


class HybridVisualStrategyPlan(ContractModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    job_id: UUID
    source_shot_expansion_artifact_id: UUID
    source_shot_expansion_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_shot_expansion_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    strategy_name: VisualStrategyName
    strategy_version: Literal["1.0.0"] = "1.0.0"
    shots: tuple[StrategicVisualShot, ...] = Field(min_length=1, max_length=500)
    summary: HybridVisualStrategySummary
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    def calculated_fingerprint(self) -> str:
        payload = self.model_dump(mode="json", exclude={"fingerprint"})
        return _sha256_json(payload)

    @model_validator(mode="after")
    def validate_plan(self) -> HybridVisualStrategyPlan:
        if self.fingerprint != self.calculated_fingerprint():
            raise ValueError("hybrid visual strategy fingerprint differs")
        if self.shots != _canonical_strategic_shots(self.shots):
            raise ValueError("hybrid visual strategy shots are not canonical")
        if len({shot.shot_id for shot in self.shots}) != len(self.shots):
            raise ValueError("hybrid visual strategy shot IDs must be unique")
        expected_quality = _quality_floor_passes(self.shots, self.strategy_name)
        if self.summary.quality_floor_pass != expected_quality:
            raise ValueError("hybrid visual strategy quality result differs from shots")
        if self.summary != _strategy_summary(
            self.shots,
            quality_floor_pass=self.summary.quality_floor_pass,
            quality_degradation_authorized=self.summary.quality_degradation_authorized,
        ):
            raise ValueError("hybrid visual strategy summary differs from shots")
        return self


@dataclass(frozen=True, slots=True)
class LegacyFullVideoStrategy:
    """Preserve the existing one-generated-video-per-shot behavior exactly."""

    name: str = "legacy_full_video_v1"

    def apply(
        self,
        shots: tuple[VisualShotAllocation, ...],
    ) -> tuple[VisualShotAllocation, ...]:
        planned: list[VisualShotAllocation] = []
        for shot in shots:
            if shot.provider_duration_seconds is None:
                raise ValueError("legacy full-video strategy requires provider duration")
            payload = shot.model_dump(mode="python")
            payload.update(
                {
                    "visual_mode": VisualMode.GENERATED_VIDEO,
                    "motion_mode": VisualMotionMode.STATIC,
                    "source_asset_id": None,
                    "importance": VisualImportance.MEDIUM,
                    "generation_priority": VisualGenerationPriority.NORMAL,
                }
            )
            planned.append(VisualShotAllocation.model_validate(payload))
        return tuple(planned)


def build_hybrid_visual_strategy_plan(
    *,
    job_id: UUID,
    source_shot_expansion_artifact_id: UUID,
    source_shot_expansion_sha256: str,
    source_shot_expansion_fingerprint: str,
    shots: tuple[VisualShotAllocation, ...],
    strategy_name: VisualStrategyName,
    policy: HybridVisualStrategyPolicy | None = None,
) -> HybridVisualStrategyPlan:
    """Select a bounded visual strategy from canonical post-TTS shots."""

    if not shots:
        raise ValueError("hybrid visual strategy requires shots")
    canonical = tuple(sorted(shots, key=_allocation_order))
    if len({shot.shot_id for shot in canonical}) != len(canonical):
        raise ValueError("visual strategy input shot IDs must be unique")
    effective_policy = policy or HybridVisualStrategyPolicy()
    if strategy_name is VisualStrategyName.FULL_VIDEO:
        allocated = LegacyFullVideoStrategy().apply(canonical)
        strategic = tuple(_strategic_from_allocation(shot) for shot in allocated)
        quality_floor_pass = True
        degradation_authorized = False
    else:
        strategic, quality_floor_pass = _build_hybrid_shots(
            canonical,
            strategy_name=strategy_name,
            policy=effective_policy,
        )
        degradation_authorized = (
            not quality_floor_pass and effective_policy.allow_quality_degradation
        )
        if not quality_floor_pass and not degradation_authorized:
            raise VisualStrategyQualityError(
                "visual strategy cannot satisfy its quality floor"
            )
    summary = _strategy_summary(
        strategic,
        quality_floor_pass=quality_floor_pass,
        quality_degradation_authorized=degradation_authorized,
    )
    provisional = HybridVisualStrategyPlan.model_construct(
        job_id=job_id,
        source_shot_expansion_artifact_id=source_shot_expansion_artifact_id,
        source_shot_expansion_sha256=source_shot_expansion_sha256,
        source_shot_expansion_fingerprint=source_shot_expansion_fingerprint,
        strategy_name=strategy_name,
        shots=strategic,
        summary=summary,
        fingerprint="0" * 64,
    )
    return HybridVisualStrategyPlan(
        job_id=job_id,
        source_shot_expansion_artifact_id=source_shot_expansion_artifact_id,
        source_shot_expansion_sha256=source_shot_expansion_sha256,
        source_shot_expansion_fingerprint=source_shot_expansion_fingerprint,
        strategy_name=strategy_name,
        shots=strategic,
        summary=summary,
        fingerprint=provisional.calculated_fingerprint(),
    )


def serialize_hybrid_visual_strategy_plan(plan: HybridVisualStrategyPlan) -> bytes:
    return _canonical_json(plan.model_dump(mode="json"))


def deserialize_hybrid_visual_strategy_plan(content: bytes) -> HybridVisualStrategyPlan:
    return HybridVisualStrategyPlan.model_validate(_strict_json_object(content))


def _build_hybrid_shots(
    shots: tuple[VisualShotAllocation, ...],
    *,
    strategy_name: VisualStrategyName,
    policy: HybridVisualStrategyPolicy,
) -> tuple[tuple[StrategicVisualShot, ...], bool]:
    reused_video_count = sum(shot.visual_mode is VisualMode.REUSED_VIDEO for shot in shots)
    desired_video_like = _desired_video_count(len(shots), strategy_name)
    desired_generated = max(0, desired_video_like - reused_video_count)
    if policy.maximum_generated_video_shots is not None:
        desired_generated = min(desired_generated, policy.maximum_generated_video_shots)
    candidates = tuple(
        shot
        for shot in shots
        if shot.source_asset_id is None and shot.provider_duration_seconds is not None
    )
    desired_generated = min(desired_generated, len(candidates))
    selected = _select_video_shot_ids(
        candidates,
        desired_generated,
        strategy_name=strategy_name,
    )
    strategic: list[StrategicVisualShot] = []
    for shot in shots:
        if shot.visual_mode in {VisualMode.REUSED_IMAGE, VisualMode.REUSED_VIDEO}:
            strategic.append(_strategic_from_allocation(shot))
        elif shot.shot_id in selected:
            strategic.append(
                _strategic_from_allocation(
                    shot,
                    visual_mode=VisualMode.GENERATED_VIDEO,
                    motion_mode=VisualMotionMode.STATIC,
                    provider_duration_seconds=shot.provider_duration_seconds,
                    preserve_provider_duration=False,
                )
            )
        else:
            strategic.append(
                _strategic_from_allocation(
                    shot,
                    visual_mode=VisualMode.GENERATED_IMAGE,
                    motion_mode=_image_motion(shot),
                    provider_duration_seconds=None,
                    preserve_provider_duration=False,
                )
            )
    result = tuple(strategic)
    return result, _quality_floor_passes(result, strategy_name)


def _desired_video_count(count: int, strategy_name: VisualStrategyName) -> int:
    if strategy_name is VisualStrategyName.HYBRID_BALANCED:
        return max(1, ceil(count * 0.50))
    if strategy_name is VisualStrategyName.HYBRID_ECONOMY:
        return max(2 if count >= 5 else 1, ceil(count * 0.30))
    return count


def _select_video_shot_ids(
    candidates: tuple[VisualShotAllocation, ...],
    count: int,
    *,
    strategy_name: VisualStrategyName,
) -> frozenset[str]:
    if count == 0:
        return frozenset()
    ranked = tuple(sorted(candidates, key=_video_rank))
    selected: list[VisualShotAllocation] = []

    hook = next(
        (shot for shot in ranked if shot.narrative_role is NarrativeRole.HOOK),
        None,
    )
    if hook is not None:
        selected.append(hook)
    if count >= 2:
        impact = next(
            (
                shot
                for shot in ranked
                if shot.narrative_role in {NarrativeRole.REVEAL, NarrativeRole.PAYOFF}
                and shot.shot_id not in {item.shot_id for item in selected}
            ),
            None,
        )
        if impact is not None:
            selected.append(impact)

    preferred = (
        _balanced_spread_candidates(candidates, count)
        if strategy_name is VisualStrategyName.HYBRID_BALANCED
        else _best_per_scene_then_ranked(ranked)
    )
    for shot in preferred:
        if len(selected) >= count:
            break
        if shot.shot_id not in {item.shot_id for item in selected}:
            selected.append(shot)
    for shot in ranked:
        if len(selected) >= count:
            break
        if shot.shot_id not in {item.shot_id for item in selected}:
            selected.append(shot)
    return frozenset(shot.shot_id for shot in selected[:count])


def _best_per_scene_then_ranked(
    ranked: tuple[VisualShotAllocation, ...],
) -> tuple[VisualShotAllocation, ...]:
    best_per_scene: dict[str, VisualShotAllocation] = {}
    for shot in ranked:
        best_per_scene.setdefault(shot.scene_id, shot)
    preferred = tuple(sorted(best_per_scene.values(), key=_video_rank))
    preferred_ids = {shot.shot_id for shot in preferred}
    return preferred + tuple(
        shot for shot in ranked if shot.shot_id not in preferred_ids
    )


def _balanced_spread_candidates(
    candidates: tuple[VisualShotAllocation, ...],
    count: int,
) -> tuple[VisualShotAllocation, ...]:
    canonical = tuple(sorted(candidates, key=_allocation_order))
    if count <= 0:
        return ()
    preferred: list[VisualShotAllocation] = []
    for index in range(count):
        start = index * len(canonical) // count
        end = max(start + 1, (index + 1) * len(canonical) // count)
        preferred.append(min(canonical[start:end], key=_video_rank))
    preferred_ids = {shot.shot_id for shot in preferred}
    return tuple(preferred) + tuple(
        shot
        for shot in sorted(canonical, key=_video_rank)
        if shot.shot_id not in preferred_ids
    )


_PRIORITY_RANK = {
    VisualGenerationPriority.LOW: 0,
    VisualGenerationPriority.NORMAL: 1,
    VisualGenerationPriority.HIGH: 2,
    VisualGenerationPriority.REQUIRED: 3,
}
_IMPORTANCE_RANK = {
    VisualImportance.LOW: 0,
    VisualImportance.MEDIUM: 1,
    VisualImportance.HIGH: 2,
    VisualImportance.HERO: 3,
}
_ROLE_RANK = {
    NarrativeRole.HOOK: 7,
    NarrativeRole.PAYOFF: 6,
    NarrativeRole.REVEAL: 5,
    NarrativeRole.ESCALATION: 4,
    NarrativeRole.SETUP: 3,
    NarrativeRole.DEVELOPMENT: 2,
    NarrativeRole.CONCLUSION: 1,
}
_FUNCTION_RANK = {
    VisualShotFunction.PRIMARY: 5,
    VisualShotFunction.RESOLVE: 4,
    VisualShotFunction.REVEAL: 3,
    VisualShotFunction.ESTABLISH: 2,
    VisualShotFunction.ADVANCE: 1,
}


def _video_rank(
    shot: VisualShotAllocation,
) -> tuple[int, int, int, int, int, int, int, str]:
    scene_index = _scene_sequence_index(shot.scene_id)
    return (
        -_PRIORITY_RANK[shot.generation_priority],
        -_IMPORTANCE_RANK[shot.importance],
        -_ROLE_RANK[shot.narrative_role],
        -(1 if shot.shot_sequence_index == 0 else 0),
        -_FUNCTION_RANK[shot.visual_function],
        scene_index,
        shot.shot_sequence_index,
        shot.shot_id,
    )


def _image_motion(shot: VisualShotAllocation) -> VisualMotionMode:
    if shot.visual_function is VisualShotFunction.ESTABLISH:
        return VisualMotionMode.PAN
    if shot.visual_function is VisualShotFunction.REVEAL:
        return VisualMotionMode.ZOOM_IN
    if shot.visual_function is VisualShotFunction.RESOLVE:
        return VisualMotionMode.ZOOM_OUT
    if shot.visual_function is VisualShotFunction.ADVANCE:
        return VisualMotionMode.PAN_AND_ZOOM
    if shot.importance in {VisualImportance.HIGH, VisualImportance.HERO}:
        return VisualMotionMode.ZOOM_IN
    return (
        VisualMotionMode.PAN_AND_ZOOM
        if shot.shot_sequence_index % 2
        else VisualMotionMode.PAN
    )


def _quality_floor_passes(
    shots: tuple[StrategicVisualShot, ...],
    strategy_name: VisualStrategyName,
) -> bool:
    video_modes = {VisualMode.GENERATED_VIDEO, VisualMode.REUSED_VIDEO}
    video = tuple(shot for shot in shots if shot.visual_mode in video_modes)
    hooks = tuple(shot for shot in shots if shot.narrative_role is NarrativeRole.HOOK)
    if hooks and not any(shot in video for shot in hooks):
        return False
    impacts = tuple(
        shot
        for shot in shots
        if shot.narrative_role in {NarrativeRole.REVEAL, NarrativeRole.PAYOFF}
    )
    minimum_impacts = 1 if len(shots) >= 5 and impacts else 0
    if sum(shot in video for shot in impacts) < minimum_impacts:
        return False
    if strategy_name is VisualStrategyName.HYBRID_BALANCED:
        if len(video) < max(1, ceil(len(shots) * 0.40)):
            return False
        if _maximum_consecutive_images(shots) > 3:
            return False
    elif len(shots) >= 5 and len(video) < 2:
        return False
    return True


def _strategy_summary(
    shots: tuple[StrategicVisualShot, ...],
    *,
    quality_floor_pass: bool,
    quality_degradation_authorized: bool,
) -> HybridVisualStrategySummary:
    return HybridVisualStrategySummary(
        visual_shot_count=len(shots),
        generated_video_shots=sum(
            shot.visual_mode is VisualMode.GENERATED_VIDEO for shot in shots
        ),
        generated_image_shots=sum(
            shot.visual_mode is VisualMode.GENERATED_IMAGE for shot in shots
        ),
        reused_video_shots=sum(
            shot.visual_mode is VisualMode.REUSED_VIDEO for shot in shots
        ),
        reused_image_shots=sum(
            shot.visual_mode is VisualMode.REUSED_IMAGE for shot in shots
        ),
        quality_floor_pass=quality_floor_pass,
        quality_degradation_authorized=quality_degradation_authorized,
        maximum_consecutive_image_shots=_maximum_consecutive_images(shots),
    )


def _maximum_consecutive_images(shots: tuple[StrategicVisualShot, ...]) -> int:
    image_modes = {VisualMode.GENERATED_IMAGE, VisualMode.REUSED_IMAGE}
    maximum = current = 0
    for shot in shots:
        current = current + 1 if shot.visual_mode in image_modes else 0
        maximum = max(maximum, current)
    return maximum


def _strategic_from_allocation(
    shot: VisualShotAllocation,
    *,
    visual_mode: VisualMode | None = None,
    motion_mode: VisualMotionMode | None = None,
    provider_duration_seconds: int | None = None,
    preserve_provider_duration: bool = True,
) -> StrategicVisualShot:
    duration = (
        shot.provider_duration_seconds
        if preserve_provider_duration
        else provider_duration_seconds
    )
    return StrategicVisualShot(
        scene_id=shot.scene_id,
        scene_sequence_index=_scene_sequence_index(shot.scene_id),
        shot_id=shot.shot_id,
        shot_sequence_index=shot.shot_sequence_index,
        visual_asset_id=shot.visual_asset_id,
        narrative_role=shot.narrative_role,
        visual_function=shot.visual_function,
        intent_key=shot.intent_key,
        usable_duration_ms=shot.usable_duration_ms,
        visual_mode=visual_mode or shot.visual_mode,
        motion_mode=motion_mode or shot.motion_mode,
        source_asset_id=shot.source_asset_id,
        importance=shot.importance,
        generation_priority=shot.generation_priority,
        provider_duration_seconds=duration,
    )


def _allocation_order(shot: VisualShotAllocation) -> tuple[int, int, str]:
    return (_scene_sequence_index(shot.scene_id), shot.shot_sequence_index, shot.shot_id)


def _canonical_strategic_shots(
    shots: tuple[StrategicVisualShot, ...],
) -> tuple[StrategicVisualShot, ...]:
    return tuple(
        sorted(
            shots,
            key=lambda shot: (
                shot.scene_sequence_index,
                shot.shot_sequence_index,
                shot.shot_id,
            ),
        )
    )


def _scene_sequence_index(scene_id: str) -> int:
    return int(scene_id.removeprefix("scene-")) - 1


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _strict_json_object(content: bytes) -> dict[str, object]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"invalid JSON constant: {value}")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    value = json.loads(
        content.decode("utf-8", errors="strict"),
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicates,
    )
    if not isinstance(value, dict):
        raise ValueError("durable visual strategy must be a JSON object")
    return value


__all__ = [
    "HybridVisualStrategyPlan",
    "HybridVisualStrategyPolicy",
    "HybridVisualStrategySummary",
    "LegacyFullVideoStrategy",
    "StrategicVisualShot",
    "VisualStrategyName",
    "VisualStrategyQualityError",
    "build_hybrid_visual_strategy_plan",
    "deserialize_hybrid_visual_strategy_plan",
    "serialize_hybrid_visual_strategy_plan",
]
