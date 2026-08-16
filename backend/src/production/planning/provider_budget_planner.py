"""Deterministic editorial and provider purchase planning.

This module plans narrative scenes independently from visual provider clips.  It
does not submit requests or know about a concrete provider transport.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from enum import StrEnum
from math import ceil

from pydantic import Field, model_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.duration_resolution import (
    DurationResolutionPolicy,
    resolve_audio_first_durations,
)
from backend.src.production.domain.visual_strategy import (
    VisualGenerationPriority,
    VisualImportance,
    VisualMode,
    VisualMotionMode,
)
from backend.src.production.scripting.models import NarrativeRole


class VideoGenerationMode(StrEnum):
    FULL_AI_VIDEO = "full_ai_video"
    HYBRID = "hybrid"
    IMAGE_MOTION = "image_motion"
    STOCK = "stock"


class VisualShotFunction(StrEnum):
    PRIMARY = "primary"
    ESTABLISH = "establish"
    ADVANCE = "advance"
    REVEAL = "reveal"
    RESOLVE = "resolve"


class EditorialSceneAllocation(ContractModel):
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    sequence_index: int = Field(ge=0, le=49)
    narrative_role: NarrativeRole
    editorial_target_ms: int = Field(gt=0, le=3_600_000)
    minimum_reasonable_ms: int = Field(gt=0, le=3_600_000)
    maximum_reasonable_ms: int = Field(gt=0, le=3_600_000)
    planned_start_ms: int = Field(ge=0, le=3_600_000)
    planned_end_ms: int = Field(gt=0, le=3_600_000)

    @model_validator(mode="after")
    def validate_interval(self) -> EditorialSceneAllocation:
        if self.planned_end_ms - self.planned_start_ms != self.editorial_target_ms:
            raise ValueError("editorial scene interval differs from target duration")
        if self.minimum_reasonable_ms > self.maximum_reasonable_ms:
            raise ValueError("minimum reasonable duration exceeds maximum")
        if not self.minimum_reasonable_ms <= self.editorial_target_ms <= self.maximum_reasonable_ms:
            raise ValueError("editorial target is outside reasonable scene bounds")
        return self


class EditorialDurationPlan(ContractModel):
    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    requested_duration_ms: int = Field(gt=0, le=3_600_000)
    scene_count: int = Field(ge=1, le=50)
    scenes: tuple[EditorialSceneAllocation, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_plan(self) -> EditorialDurationPlan:
        if self.scene_count != len(self.scenes):
            raise ValueError("editorial scene count differs from allocations")
        if tuple(scene.sequence_index for scene in self.scenes) != tuple(range(self.scene_count)):
            raise ValueError("editorial scenes must be ordered deterministically")
        if sum(scene.editorial_target_ms for scene in self.scenes) != self.requested_duration_ms:
            raise ValueError("editorial allocations must consume the requested duration")
        if self.scenes[0].planned_start_ms != 0 or self.scenes[-1].planned_end_ms != self.requested_duration_ms:
            raise ValueError("editorial allocation must span the requested duration")
        if any(
            before.planned_end_ms != after.planned_start_ms
            for before, after in zip(self.scenes, self.scenes[1:], strict=False)
        ):
            raise ValueError("editorial scenes must be contiguous")
        return self


class ResolvedNarrativeScene(ContractModel):
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    sequence_index: int = Field(ge=0, le=49)
    narrative_role: NarrativeRole
    editorial_target_ms: int = Field(gt=0, le=3_600_000)
    actual_narration_ms: int = Field(ge=0, le=3_600_000)
    resolved_duration_ms: int = Field(gt=0, le=3_600_000)

    @model_validator(mode="after")
    def validate_resolution(self) -> ResolvedNarrativeScene:
        if self.resolved_duration_ms != max(self.editorial_target_ms, self.actual_narration_ms):
            raise ValueError("resolved duration must follow audio-first policy")
        return self


class BoundVisualShot(ContractModel):
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    scene_sequence_index: int = Field(ge=0, le=49)
    shot_id: str = Field(pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$")
    shot_sequence_index: int = Field(ge=0, le=99)
    visual_asset_id: str = Field(
        pattern=r"^asset-s[0-9]{3}-q[0-9]{3}-v[0-9]{3}$"
    )
    visual_intent_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    usable_duration_ms: int = Field(gt=0, le=600_000)


class VisualShotAllocation(ContractModel):
    """One editorial visual slot, independent from a provider purchase.

    Legacy full-video values are omitted from canonical serialization so old
    shot-expansion payloads and fingerprints retain their exact shape.
    """

    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    shot_id: str = Field(pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$")
    shot_sequence_index: int = Field(ge=0, le=49)
    visual_asset_id: str = Field(
        pattern=r"^asset-s[0-9]{3}-q[0-9]{3}-v[0-9]{3}$"
    )
    narrative_role: NarrativeRole
    visual_function: VisualShotFunction
    intent_key: str = Field(min_length=1, max_length=300)
    usable_duration_ms: int = Field(gt=0, le=600_000)
    visual_mode: VisualMode = Field(
        default=VisualMode.GENERATED_VIDEO,
        exclude_if=lambda value: value is VisualMode.GENERATED_VIDEO,
    )
    motion_mode: VisualMotionMode = Field(
        default=VisualMotionMode.STATIC,
        exclude_if=lambda value: value is VisualMotionMode.STATIC,
    )
    source_asset_id: str | None = Field(
        default=None,
        min_length=3,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        exclude_if=lambda value: value is None,
    )
    importance: VisualImportance = Field(
        default=VisualImportance.MEDIUM,
        exclude_if=lambda value: value is VisualImportance.MEDIUM,
    )
    generation_priority: VisualGenerationPriority = Field(
        default=VisualGenerationPriority.NORMAL,
        exclude_if=lambda value: value is VisualGenerationPriority.NORMAL,
    )
    provider_duration_seconds: int | None = Field(
        default=None,
        gt=0,
        le=600,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def validate_visual_strategy(self) -> VisualShotAllocation:
        reused = self.visual_mode in {
            VisualMode.REUSED_IMAGE,
            VisualMode.REUSED_VIDEO,
        }
        if reused != (self.source_asset_id is not None):
            raise ValueError("only reused visual shots require source_asset_id")
        if self.visual_mode is VisualMode.GENERATED_VIDEO:
            if self.provider_duration_seconds is None:
                raise ValueError("generated video requires provider purchase duration")
        elif self.provider_duration_seconds is not None:
            raise ValueError("only generated video may define provider purchase duration")
        if (
            self.motion_mode is not VisualMotionMode.STATIC
            and self.visual_mode
            not in {VisualMode.GENERATED_IMAGE, VisualMode.REUSED_IMAGE}
        ):
            raise ValueError("local pan and zoom motion requires an image visual mode")
        return self


class AudioFirstNarrativePlan(ContractModel):
    requested_duration_ms: int = Field(gt=0, le=3_600_000)
    resolved_duration_ms: int = Field(gt=0, le=3_600_000)
    maximum_allowed_duration_ms: int = Field(gt=0, le=3_600_000)
    accepted: bool
    scenes: tuple[ResolvedNarrativeScene, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_plan(self) -> AudioFirstNarrativePlan:
        if sum(scene.editorial_target_ms for scene in self.scenes) != self.requested_duration_ms:
            raise ValueError("audio-first editorial targets differ from request")
        if sum(scene.resolved_duration_ms for scene in self.scenes) != self.resolved_duration_ms:
            raise ValueError("audio-first scene durations differ from total")
        if not self.accepted:
            raise ValueError("measured narration duration is always accepted")
        return self


class VisualClipPurchase(ContractModel):
    clip_id: str = Field(
        pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}-clip-[0-9]{3}$"
    )
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    shot_id: str = Field(pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$")
    visual_asset_id: str = Field(
        pattern=r"^asset-s[0-9]{3}-q[0-9]{3}-v[0-9]{3}$"
    )
    sequence_index: int = Field(ge=0, le=49)
    visual_intent_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider_duration_seconds: int = Field(gt=0, le=600)
    usable_duration_ms: int = Field(gt=0, le=600_000)
    adaptation: str = Field(pattern=r"^(none|trim)$")
    estimated_cost_usd: Decimal = Field(ge=0, max_digits=18, decimal_places=9)


class SceneProviderPurchasePlan(ContractModel):
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    resolved_duration_ms: int = Field(gt=0, le=600_000)
    clips: tuple[VisualClipPurchase, ...] = Field(min_length=1, max_length=50)
    purchased_seconds: int = Field(gt=0, le=30_000)
    estimated_cost_usd: Decimal = Field(ge=0, max_digits=18, decimal_places=9)

    @model_validator(mode="after")
    def validate_coverage(self) -> SceneProviderPurchasePlan:
        if sum(clip.provider_duration_seconds for clip in self.clips) != self.purchased_seconds:
            raise ValueError("scene purchase seconds differ from clips")
        if sum(clip.usable_duration_ms for clip in self.clips) != self.resolved_duration_ms:
            raise ValueError("visual clip usable durations must equal the resolved scene")
        if sum(
            (clip.estimated_cost_usd for clip in self.clips), Decimal("0")
        ) != self.estimated_cost_usd:
            raise ValueError("scene estimated cost differs from clips")
        return self


class VideoProviderPurchasePlan(ContractModel):
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=300)
    generation_mode: VideoGenerationMode = VideoGenerationMode.FULL_AI_VIDEO
    price_per_second_usd: Decimal = Field(gt=0, max_digits=18, decimal_places=9)
    scenes: tuple[SceneProviderPurchasePlan, ...] = Field(min_length=1, max_length=50)
    total_clip_count: int = Field(gt=0, le=500)
    total_purchased_seconds: int = Field(gt=0, le=30_000)
    estimated_cost_usd: Decimal = Field(ge=0, max_digits=18, decimal_places=9)
    max_requests_per_job: int = Field(gt=0, le=500)
    maximum_authorized_cost_per_request_usd: Decimal = Field(
        gt=0,
        max_digits=18,
        decimal_places=9,
    )
    maximum_authorized_cost_usd: Decimal = Field(gt=0, max_digits=18, decimal_places=9)
    accepted: bool

    def fingerprint(self) -> str:
        content = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    @model_validator(mode="after")
    def validate_totals(self) -> VideoProviderPurchasePlan:
        if self.total_clip_count != sum(len(scene.clips) for scene in self.scenes):
            raise ValueError("total clip count differs from scenes")
        if self.total_purchased_seconds != sum(scene.purchased_seconds for scene in self.scenes):
            raise ValueError("total purchased seconds differ from scenes")
        if self.estimated_cost_usd != sum(
            (scene.estimated_cost_usd for scene in self.scenes), Decimal("0")
        ):
            raise ValueError("estimated cost differs from scene costs")
        if self.estimated_cost_usd != self.price_per_second_usd * self.total_purchased_seconds:
            raise ValueError("estimated cost differs from purchased seconds")
        if self.accepted != (
            self.total_clip_count <= self.max_requests_per_job
            and all(
                clip.estimated_cost_usd
                <= self.maximum_authorized_cost_per_request_usd
                for scene in self.scenes
                for clip in scene.clips
            )
            and self.estimated_cost_usd <= self.maximum_authorized_cost_usd
        ):
            raise ValueError("purchase plan acceptance is inconsistent")
        return self


class VideoPurchaseBudgetError(ValueError):
    """Raised before submission when a deterministic purchase plan is unsafe."""

    def __init__(self, message: str, *, plan: VideoProviderPurchasePlan) -> None:
        self.plan = plan
        super().__init__(message)


_ROLE_WEIGHTS: dict[NarrativeRole, Decimal] = {
    NarrativeRole.HOOK: Decimal("0.85"),
    NarrativeRole.SETUP: Decimal("0.95"),
    NarrativeRole.DEVELOPMENT: Decimal("1.15"),
    NarrativeRole.ESCALATION: Decimal("1.10"),
    NarrativeRole.REVEAL: Decimal("1.05"),
    NarrativeRole.PAYOFF: Decimal("0.90"),
    NarrativeRole.CONCLUSION: Decimal("0.90"),
}


def propose_scene_count(requested_duration_ms: int, explicit_scene_count: int | None = None) -> int:
    """Suggest a count while respecting an explicit user choice."""

    if requested_duration_ms <= 0:
        raise ValueError("requested duration must be positive")
    if explicit_scene_count is not None:
        if not 1 <= explicit_scene_count <= 50:
            raise ValueError("scene count must be between one and fifty")
        return explicit_scene_count
    seconds = requested_duration_ms / 1_000
    if seconds <= 15:
        return 2
    if seconds <= 35:
        return 4
    if seconds <= 60:
        return 6
    return min(50, max(2, ceil(seconds / 10)))


def allocate_editorial_duration_plan(
    *,
    requested_duration_ms: int,
    scene_count: int,
    narrative_roles: tuple[NarrativeRole, ...],
    minimum_scene_duration_ms: int = 1_000,
) -> EditorialDurationPlan:
    """Allocate contiguous time using narrative-role weights, not uniform division."""

    if len(narrative_roles) != scene_count:
        raise ValueError("narrative roles must match scene count")
    if requested_duration_ms < scene_count * minimum_scene_duration_ms:
        raise ValueError("requested duration cannot satisfy scene minimums")
    distributable = requested_duration_ms - scene_count * minimum_scene_duration_ms
    weights = tuple(_ROLE_WEIGHTS[role] for role in narrative_roles)
    total_weight = sum(weights, Decimal("0"))
    numerators = tuple(Decimal(distributable) * weight for weight in weights)
    extras = [int(value // total_weight) for value in numerators]
    remainder = distributable - sum(extras)
    order = sorted(
        range(scene_count),
        key=lambda index: (-(numerators[index] % total_weight), index),
    )
    for index in order[:remainder]:
        extras[index] += 1

    scenes: list[EditorialSceneAllocation] = []
    start = 0
    average = requested_duration_ms / scene_count
    maximum = max(minimum_scene_duration_ms, round(average * 2))
    for index, (role, extra) in enumerate(zip(narrative_roles, extras, strict=True)):
        duration = minimum_scene_duration_ms + extra
        scenes.append(
            EditorialSceneAllocation(
                scene_id=f"scene-{index + 1:03d}",
                sequence_index=index,
                narrative_role=role,
                editorial_target_ms=duration,
                minimum_reasonable_ms=minimum_scene_duration_ms,
                maximum_reasonable_ms=maximum,
                planned_start_ms=start,
                planned_end_ms=start + duration,
            )
        )
        start += duration
    return EditorialDurationPlan(
        requested_duration_ms=requested_duration_ms,
        scene_count=scene_count,
        scenes=tuple(scenes),
    )


def resolve_editorial_audio_first(
    editorial_plan: EditorialDurationPlan,
    narration_durations_ms: tuple[int, ...],
    policy: DurationResolutionPolicy,
) -> AudioFirstNarrativePlan:
    """Resolve actual narration while reusing the canonical audio-first policy."""

    if len(narration_durations_ms) != editorial_plan.scene_count:
        raise ValueError("narration durations must match editorial scenes")
    resolution = resolve_audio_first_durations(
        requested_target_duration_ms=editorial_plan.requested_duration_ms,
        planned_scene_durations_ms=tuple(
            scene.editorial_target_ms for scene in editorial_plan.scenes
        ),
        narration_scene_durations_ms=narration_durations_ms,
        policy=policy,
    )
    return AudioFirstNarrativePlan(
        requested_duration_ms=resolution.requested_target_duration_ms,
        resolved_duration_ms=resolution.resolved_duration_ms,
        maximum_allowed_duration_ms=resolution.maximum_allowed_duration_ms,
        accepted=True,
        scenes=tuple(
            ResolvedNarrativeScene(
                scene_id=scene.scene_id,
                sequence_index=scene.sequence_index,
                narrative_role=scene.narrative_role,
                editorial_target_ms=scene.editorial_target_ms,
                actual_narration_ms=narration,
                resolved_duration_ms=resolved,
            )
            for scene, narration, resolved in zip(
                editorial_plan.scenes,
                narration_durations_ms,
                resolution.resolved_scene_durations_ms,
                strict=True,
            )
        ),
    )


def cover_duration_with_provider_clips(
    required_duration_ms: int,
    supported_durations_seconds: tuple[int, ...],
) -> tuple[int, ...]:
    """Find minimum purchased seconds, then minimum request count, deterministically."""

    if required_duration_ms <= 0:
        raise ValueError("required duration must be positive")
    durations = tuple(sorted(set(supported_durations_seconds)))
    if not durations or any(duration <= 0 for duration in durations):
        raise ValueError("supported provider durations must be positive")
    maximum = max(durations)
    upper_bound = ceil(required_duration_ms / 1_000) + maximum
    best: dict[int, tuple[int, ...]] = {0: ()}
    for total in range(1, upper_bound + 1):
        candidates = [
            best[total - duration] + (duration,)
            for duration in durations
            if total >= duration and total - duration in best
        ]
        if candidates:
            best[total] = min(candidates, key=lambda item: (sum(item), len(item), tuple(-x for x in item)))
    eligible = [plan for total, plan in best.items() if total * 1_000 >= required_duration_ms]
    if not eligible:
        raise ValueError("provider durations cannot cover the required scene")
    return min(eligible, key=lambda item: (sum(item), len(item), tuple(-x for x in item)))


def build_video_purchase_plan(
    *,
    resolved_plan: AudioFirstNarrativePlan,
    provider: str,
    model: str,
    supported_durations_seconds: tuple[int, ...],
    price_per_second_usd: Decimal,
    max_requests_per_job: int,
    maximum_authorized_cost_per_request_usd: Decimal,
    maximum_authorized_cost_usd: Decimal,
    generation_mode: VideoGenerationMode = VideoGenerationMode.FULL_AI_VIDEO,
) -> VideoProviderPurchasePlan:
    """Build all visual purchases before any provider submission is possible."""

    if price_per_second_usd <= 0:
        raise ValueError("provider price must be positive")
    scenes: list[SceneProviderPurchasePlan] = []
    for scene in resolved_plan.scenes:
        allocations = allocate_visual_shots(
            scene,
            supported_durations_seconds=supported_durations_seconds,
        )
        clips: list[VisualClipPurchase] = []
        for allocation in allocations:
            duration = allocation.provider_duration_seconds
            if (
                allocation.visual_mode is not VisualMode.GENERATED_VIDEO
                or duration is None
            ):
                raise ValueError("full-video purchase planning requires generated video shots")
            clips.append(
                VisualClipPurchase(
                    clip_id=f"{allocation.shot_id}-clip-001",
                    scene_id=allocation.scene_id,
                    shot_id=allocation.shot_id,
                    visual_asset_id=allocation.visual_asset_id,
                    sequence_index=allocation.shot_sequence_index,
                    visual_intent_sha256=hashlib.sha256(
                        allocation.intent_key.encode()
                    ).hexdigest(),
                    source_image_sha256=hashlib.sha256(
                        f"source:{allocation.visual_asset_id}".encode()
                    ).hexdigest(),
                    provider_duration_seconds=duration,
                    usable_duration_ms=allocation.usable_duration_ms,
                    adaptation=(
                        "trim"
                        if allocation.usable_duration_ms
                        < duration * 1_000
                        else "none"
                    ),
                    estimated_cost_usd=(
                        price_per_second_usd
                        * duration
                    ),
                )
            )
        purchased = sum(clip.provider_duration_seconds for clip in clips)
        scenes.append(
            SceneProviderPurchasePlan(
                scene_id=scene.scene_id,
                resolved_duration_ms=scene.resolved_duration_ms,
                clips=tuple(clips),
                purchased_seconds=purchased,
                estimated_cost_usd=price_per_second_usd * purchased,
            )
        )
    total_clips = sum(len(scene.clips) for scene in scenes)
    total_seconds = sum(scene.purchased_seconds for scene in scenes)
    total_cost = price_per_second_usd * total_seconds
    plan = VideoProviderPurchasePlan(
        provider=provider,
        model=model,
        generation_mode=generation_mode,
        price_per_second_usd=price_per_second_usd,
        scenes=tuple(scenes),
        total_clip_count=total_clips,
        total_purchased_seconds=total_seconds,
        estimated_cost_usd=total_cost,
        max_requests_per_job=max_requests_per_job,
        maximum_authorized_cost_per_request_usd=(
            maximum_authorized_cost_per_request_usd
        ),
        maximum_authorized_cost_usd=maximum_authorized_cost_usd,
        accepted=(
            total_clips <= max_requests_per_job
            and all(
                clip.estimated_cost_usd
                <= maximum_authorized_cost_per_request_usd
                for scene in scenes
                for clip in scene.clips
            )
            and total_cost <= maximum_authorized_cost_usd
        ),
    )
    return plan


def allocate_visual_shots(
    scene: ResolvedNarrativeScene,
    *,
    supported_durations_seconds: tuple[int, ...],
) -> tuple[VisualShotAllocation, ...]:
    """Derive stable visual-shot slots from one resolved narrative scene."""

    durations = cover_duration_with_provider_clips(
        scene.resolved_duration_ms,
        supported_durations_seconds,
    )
    remaining = scene.resolved_duration_ms
    allocations: list[VisualShotAllocation] = []
    for index, duration in enumerate(durations):
        usable = min(remaining, duration * 1_000)
        shot_number = index + 1
        if len(durations) == 1:
            function = VisualShotFunction.PRIMARY
        elif index == 0:
            function = VisualShotFunction.ESTABLISH
        elif index == len(durations) - 1 and scene.narrative_role in {
            NarrativeRole.REVEAL,
            NarrativeRole.PAYOFF,
            NarrativeRole.CONCLUSION,
        }:
            function = VisualShotFunction.RESOLVE
        elif index == len(durations) - 1:
            function = VisualShotFunction.REVEAL
        else:
            function = VisualShotFunction.ADVANCE
        shot_id = f"{scene.scene_id}-shot-{shot_number:03d}"
        allocations.append(
            VisualShotAllocation(
                scene_id=scene.scene_id,
                shot_id=shot_id,
                shot_sequence_index=index,
                visual_asset_id=(
                    f"asset-s{scene.sequence_index + 1:03d}-q{shot_number:03d}-v001"
                ),
                narrative_role=scene.narrative_role,
                visual_function=function,
                intent_key=(
                    f"{scene.scene_id}:{scene.narrative_role.value}:{function.value}:"
                    f"{shot_number}-of-{len(durations)}"
                ),
                usable_duration_ms=usable,
                visual_mode=VisualMode.GENERATED_VIDEO,
                motion_mode=VisualMotionMode.STATIC,
                importance=VisualImportance.MEDIUM,
                generation_priority=VisualGenerationPriority.NORMAL,
                provider_duration_seconds=duration,
            )
        )
        remaining -= usable
    return tuple(allocations)


def build_bound_video_purchase_plan(
    *,
    shots: tuple[BoundVisualShot, ...],
    provider: str,
    model: str,
    supported_durations_seconds: tuple[int, ...],
    price_per_second_usd: Decimal,
    max_requests_per_job: int,
    maximum_authorized_cost_per_request_usd: Decimal,
    maximum_authorized_cost_usd: Decimal,
    generation_mode: VideoGenerationMode = VideoGenerationMode.FULL_AI_VIDEO,
) -> VideoProviderPurchasePlan:
    """Bind discrete purchases to already-approved distinct visual shots."""

    if not shots:
        raise ValueError("bound video purchase planning requires visual shots")
    ordered = tuple(
        sorted(
            shots,
            key=lambda item: (
                item.scene_sequence_index,
                item.shot_sequence_index,
                item.visual_asset_id,
            ),
        )
    )
    if ordered != shots:
        raise ValueError("bound visual shots must be deterministically ordered")
    if len({shot.visual_asset_id for shot in shots}) != len(shots):
        raise ValueError("bound visual assets must be unique")
    by_scene: dict[str, list[BoundVisualShot]] = {}
    for shot in shots:
        by_scene.setdefault(shot.scene_id, []).append(shot)
    scenes: list[SceneProviderPurchasePlan] = []
    for scene_id, scene_shots in by_scene.items():
        hashes = tuple(shot.visual_intent_sha256 for shot in scene_shots)
        if len(hashes) > 1 and len(set(hashes)) != len(hashes):
            raise ValueError("consecutive visual shots require distinct visual intent")
        clips: list[VisualClipPurchase] = []
        for shot in scene_shots:
            coverage = cover_duration_with_provider_clips(
                shot.usable_duration_ms,
                supported_durations_seconds,
            )
            if len(coverage) != 1:
                raise ValueError(
                    "visual shot exceeds one provider clip and must be split upstream"
                )
            duration = coverage[0]
            clips.append(
                VisualClipPurchase(
                    clip_id=f"{shot.shot_id}-clip-001",
                    scene_id=shot.scene_id,
                    shot_id=shot.shot_id,
                    visual_asset_id=shot.visual_asset_id,
                    sequence_index=shot.shot_sequence_index,
                    visual_intent_sha256=shot.visual_intent_sha256,
                    source_image_sha256=shot.source_image_sha256,
                    provider_duration_seconds=duration,
                    usable_duration_ms=shot.usable_duration_ms,
                    adaptation=(
                        "trim"
                        if shot.usable_duration_ms < duration * 1_000
                        else "none"
                    ),
                    estimated_cost_usd=price_per_second_usd * duration,
                )
            )
        purchased = sum(clip.provider_duration_seconds for clip in clips)
        scenes.append(
            SceneProviderPurchasePlan(
                scene_id=scene_id,
                resolved_duration_ms=sum(clip.usable_duration_ms for clip in clips),
                clips=tuple(clips),
                purchased_seconds=purchased,
                estimated_cost_usd=price_per_second_usd * purchased,
            )
        )
    total_clips = sum(len(scene.clips) for scene in scenes)
    total_seconds = sum(scene.purchased_seconds for scene in scenes)
    total_cost = price_per_second_usd * total_seconds
    return VideoProviderPurchasePlan(
        provider=provider,
        model=model,
        generation_mode=generation_mode,
        price_per_second_usd=price_per_second_usd,
        scenes=tuple(scenes),
        total_clip_count=total_clips,
        total_purchased_seconds=total_seconds,
        estimated_cost_usd=total_cost,
        max_requests_per_job=max_requests_per_job,
        maximum_authorized_cost_per_request_usd=(
            maximum_authorized_cost_per_request_usd
        ),
        maximum_authorized_cost_usd=maximum_authorized_cost_usd,
        accepted=(
            total_clips <= max_requests_per_job
            and all(
                clip.estimated_cost_usd
                <= maximum_authorized_cost_per_request_usd
                for scene in scenes
                for clip in scene.clips
            )
            and total_cost <= maximum_authorized_cost_usd
        ),
    )


def authorize_video_purchase_plan(plan: VideoProviderPurchasePlan) -> VideoProviderPurchasePlan:
    if not plan.accepted:
        raise VideoPurchaseBudgetError(
            "video purchase plan exceeds request or cost authorization",
            plan=plan,
        )
    return plan


__all__ = [
    "AudioFirstNarrativePlan",
    "BoundVisualShot",
    "EditorialDurationPlan",
    "EditorialSceneAllocation",
    "ResolvedNarrativeScene",
    "SceneProviderPurchasePlan",
    "VideoGenerationMode",
    "VideoProviderPurchasePlan",
    "VideoPurchaseBudgetError",
    "VisualClipPurchase",
    "VisualShotAllocation",
    "VisualShotFunction",
    "allocate_editorial_duration_plan",
    "allocate_visual_shots",
    "authorize_video_purchase_plan",
    "build_video_purchase_plan",
    "build_bound_video_purchase_plan",
    "cover_duration_with_provider_clips",
    "propose_scene_count",
    "resolve_editorial_audio_first",
]
