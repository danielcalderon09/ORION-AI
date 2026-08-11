"""Deterministic aggregate image and video exposure planning before asset spend."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.visual_strategy import VisualMode
from backend.src.production.planning.visual_strategy import (
    HybridVisualStrategyPlan,
    VisualStrategyName,
)


class ImageRequirementKind(StrEnum):
    VIDEO_FIRST_FRAME = "video_first_frame"
    IMAGE_VISUAL = "image_visual"


class PlannedImageRequest(ContractModel):
    shot_id: str = Field(pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$")
    visual_asset_id: str = Field(pattern=r"^asset-s[0-9]{3}-q[0-9]{3}-v[0-9]{3}$")
    requirement: ImageRequirementKind
    estimated_cost_usd: Decimal = Field(ge=0, max_digits=18, decimal_places=9)

    @field_validator("estimated_cost_usd", mode="before")
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("image estimates must use Decimal text")
        return value


class PlannedVideoRequest(ContractModel):
    shot_id: str = Field(pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$")
    visual_asset_id: str = Field(pattern=r"^asset-s[0-9]{3}-q[0-9]{3}-v[0-9]{3}$")
    usable_duration_ms: int = Field(gt=0, le=600_000)
    provider_duration_seconds: int = Field(gt=0, le=600)
    estimated_cost_usd: Decimal = Field(ge=0, max_digits=18, decimal_places=9)

    @field_validator("estimated_cost_usd", mode="before")
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("video estimates must use Decimal text")
        return value

    @model_validator(mode="after")
    def validate_coverage(self) -> PlannedVideoRequest:
        if self.provider_duration_seconds * 1_000 < self.usable_duration_ms:
            raise ValueError("planned video purchase undercovers its shot")
        return self


class HybridVisualBudgetAuthorization(ContractModel):
    estimated_image_cost_per_request_usd: Decimal = Field(
        gt=0, max_digits=18, decimal_places=9
    )
    video_price_per_second_usd: Decimal = Field(gt=0, max_digits=18, decimal_places=9)
    maximum_image_requests: int = Field(ge=0, le=500)
    maximum_video_requests: int = Field(ge=0, le=500)
    maximum_authorized_image_cost_usd: Decimal = Field(
        ge=0, max_digits=18, decimal_places=9
    )
    maximum_authorized_video_cost_per_request_usd: Decimal = Field(
        ge=0, max_digits=18, decimal_places=9
    )
    maximum_authorized_video_cost_usd: Decimal = Field(
        ge=0, max_digits=18, decimal_places=9
    )
    maximum_authorized_total_visual_cost_usd: Decimal = Field(
        ge=0, max_digits=18, decimal_places=9
    )

    @field_validator(
        "estimated_image_cost_per_request_usd",
        "video_price_per_second_usd",
        "maximum_authorized_image_cost_usd",
        "maximum_authorized_video_cost_per_request_usd",
        "maximum_authorized_video_cost_usd",
        "maximum_authorized_total_visual_cost_usd",
        mode="before",
    )
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("visual budget authorization must use Decimal text")
        return value


class AggregateVisualBudgetPlan(ContractModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    job_id: UUID
    source_strategy_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    strategy: VisualStrategyName
    visual_shot_count: int = Field(gt=0, le=500)
    generated_video_shots: int = Field(ge=0, le=500)
    generated_image_shots: int = Field(ge=0, le=500)
    reused_video_shots: int = Field(ge=0, le=500)
    reused_image_shots: int = Field(ge=0, le=500)
    image_requirements: tuple[PlannedImageRequest, ...] = Field(max_length=500)
    video_requirements: tuple[PlannedVideoRequest, ...] = Field(max_length=500)
    image_requests: int = Field(ge=0, le=500)
    video_requests: int = Field(ge=0, le=500)
    purchased_video_seconds: int = Field(ge=0, le=300_000)
    estimated_image_cost_per_request_usd: Decimal = Field(
        gt=0, max_digits=18, decimal_places=9
    )
    video_price_per_second_usd: Decimal = Field(gt=0, max_digits=18, decimal_places=9)
    estimated_image_cost_usd: Decimal = Field(ge=0, max_digits=18, decimal_places=9)
    estimated_video_cost_usd: Decimal = Field(ge=0, max_digits=18, decimal_places=9)
    estimated_total_visual_cost_usd: Decimal = Field(
        ge=0, max_digits=18, decimal_places=9
    )
    maximum_image_requests: int = Field(ge=0, le=500)
    maximum_video_requests: int = Field(ge=0, le=500)
    maximum_authorized_image_cost_usd: Decimal = Field(
        ge=0, max_digits=18, decimal_places=9
    )
    maximum_authorized_video_cost_per_request_usd: Decimal = Field(
        ge=0, max_digits=18, decimal_places=9
    )
    maximum_authorized_video_cost_usd: Decimal = Field(
        ge=0, max_digits=18, decimal_places=9
    )
    maximum_authorized_total_visual_cost_usd: Decimal = Field(
        ge=0, max_digits=18, decimal_places=9
    )
    quality_floor_pass: bool
    quality_degradation_authorized: bool
    budget_pass: bool
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator(
        "estimated_image_cost_usd",
        "estimated_video_cost_usd",
        "estimated_total_visual_cost_usd",
        "estimated_image_cost_per_request_usd",
        "video_price_per_second_usd",
        "maximum_authorized_image_cost_usd",
        "maximum_authorized_video_cost_per_request_usd",
        "maximum_authorized_video_cost_usd",
        "maximum_authorized_total_visual_cost_usd",
        mode="before",
    )
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("aggregate visual costs must use Decimal text")
        return value

    def calculated_fingerprint(self) -> str:
        payload = self.model_dump(mode="json", exclude={"fingerprint"})
        return hashlib.sha256(_canonical_json(payload)).hexdigest()

    @model_validator(mode="after")
    def validate_plan(self) -> AggregateVisualBudgetPlan:
        if self.fingerprint != self.calculated_fingerprint():
            raise ValueError("aggregate visual budget fingerprint differs")
        if self.visual_shot_count != (
            self.generated_video_shots
            + self.generated_image_shots
            + self.reused_video_shots
            + self.reused_image_shots
        ):
            raise ValueError("aggregate visual shot counts differ")
        if self.image_requests != len(self.image_requirements):
            raise ValueError("image request count differs from requirements")
        if self.video_requests != len(self.video_requirements):
            raise ValueError("video request count differs from requirements")
        if self.image_requests != self.generated_video_shots + self.generated_image_shots:
            raise ValueError("generated visual shots must each require exactly one image")
        if self.video_requests != self.generated_video_shots:
            raise ValueError("only generated video shots may require video purchases")
        if len({item.shot_id for item in self.image_requirements}) != self.image_requests:
            raise ValueError("one visual shot cannot require duplicate image requests")
        if len({item.shot_id for item in self.video_requirements}) != self.video_requests:
            raise ValueError("one visual shot cannot require duplicate video requests")
        if tuple(item.shot_id for item in self.image_requirements) != tuple(
            sorted(item.shot_id for item in self.image_requirements)
        ):
            raise ValueError("image requirements must be canonically ordered")
        if tuple(item.shot_id for item in self.video_requirements) != tuple(
            sorted(item.shot_id for item in self.video_requirements)
        ):
            raise ValueError("video requirements must be canonically ordered")
        if any(
            item.estimated_cost_usd != self.estimated_image_cost_per_request_usd
            for item in self.image_requirements
        ):
            raise ValueError("image requirement cost differs from unit estimate")
        if any(
            item.estimated_cost_usd
            != self.video_price_per_second_usd * item.provider_duration_seconds
            for item in self.video_requirements
        ):
            raise ValueError("video requirement cost differs from unit price")
        if self.purchased_video_seconds != sum(
            item.provider_duration_seconds for item in self.video_requirements
        ):
            raise ValueError("purchased video seconds differ from requirements")
        if self.estimated_image_cost_usd != sum(
            (item.estimated_cost_usd for item in self.image_requirements),
            Decimal("0"),
        ):
            raise ValueError("estimated image cost differs from requirements")
        if self.estimated_video_cost_usd != sum(
            (item.estimated_cost_usd for item in self.video_requirements),
            Decimal("0"),
        ):
            raise ValueError("estimated video cost differs from requirements")
        if self.estimated_total_visual_cost_usd != (
            self.estimated_image_cost_usd + self.estimated_video_cost_usd
        ):
            raise ValueError("estimated total visual cost differs")
        expected_pass = (
            self.image_requests <= self.maximum_image_requests
            and self.video_requests <= self.maximum_video_requests
            and self.estimated_image_cost_usd
            <= self.maximum_authorized_image_cost_usd
            and all(
                item.estimated_cost_usd
                <= self.maximum_authorized_video_cost_per_request_usd
                for item in self.video_requirements
            )
            and self.estimated_video_cost_usd
            <= self.maximum_authorized_video_cost_usd
            and self.estimated_total_visual_cost_usd
            <= self.maximum_authorized_total_visual_cost_usd
            and (self.quality_floor_pass or self.quality_degradation_authorized)
        )
        if self.budget_pass != expected_pass:
            raise ValueError("aggregate visual budget acceptance is inconsistent")
        return self


class AggregateVisualBudgetError(ValueError):
    def __init__(self, plan: AggregateVisualBudgetPlan) -> None:
        self.plan = plan
        super().__init__("aggregate visual budget is not authorized")


def build_aggregate_visual_budget_plan(
    *,
    strategy_plan: HybridVisualStrategyPlan,
    authorization: HybridVisualBudgetAuthorization,
) -> AggregateVisualBudgetPlan:
    """Count each image exactly once and every generated video purchase only."""

    image_requirements: list[PlannedImageRequest] = []
    video_requirements: list[PlannedVideoRequest] = []
    for shot in strategy_plan.shots:
        if shot.visual_mode in {VisualMode.GENERATED_VIDEO, VisualMode.GENERATED_IMAGE}:
            image_requirements.append(
                PlannedImageRequest(
                    shot_id=shot.shot_id,
                    visual_asset_id=shot.visual_asset_id,
                    requirement=(
                        ImageRequirementKind.VIDEO_FIRST_FRAME
                        if shot.visual_mode is VisualMode.GENERATED_VIDEO
                        else ImageRequirementKind.IMAGE_VISUAL
                    ),
                    estimated_cost_usd=(
                        authorization.estimated_image_cost_per_request_usd
                    ),
                )
            )
        if shot.visual_mode is VisualMode.GENERATED_VIDEO:
            duration = shot.provider_duration_seconds
            if duration is None:
                raise ValueError("generated video strategy lacks provider duration")
            video_requirements.append(
                PlannedVideoRequest(
                    shot_id=shot.shot_id,
                    visual_asset_id=shot.visual_asset_id,
                    usable_duration_ms=shot.usable_duration_ms,
                    provider_duration_seconds=duration,
                    estimated_cost_usd=(
                        authorization.video_price_per_second_usd * duration
                    ),
                )
            )
    images = tuple(image_requirements)
    videos = tuple(video_requirements)
    image_cost = sum((item.estimated_cost_usd for item in images), Decimal("0"))
    video_cost = sum((item.estimated_cost_usd for item in videos), Decimal("0"))
    total_cost = image_cost + video_cost
    quality_pass = strategy_plan.summary.quality_floor_pass
    degradation = strategy_plan.summary.quality_degradation_authorized
    pass_value = (
        len(images) <= authorization.maximum_image_requests
        and len(videos) <= authorization.maximum_video_requests
        and image_cost <= authorization.maximum_authorized_image_cost_usd
        and all(
            item.estimated_cost_usd
            <= authorization.maximum_authorized_video_cost_per_request_usd
            for item in videos
        )
        and video_cost <= authorization.maximum_authorized_video_cost_usd
        and total_cost <= authorization.maximum_authorized_total_visual_cost_usd
        and (quality_pass or degradation)
    )
    purchased_seconds = sum(item.provider_duration_seconds for item in videos)
    provisional = AggregateVisualBudgetPlan.model_construct(
        job_id=strategy_plan.job_id,
        source_strategy_fingerprint=strategy_plan.fingerprint,
        strategy=strategy_plan.strategy_name,
        visual_shot_count=strategy_plan.summary.visual_shot_count,
        generated_video_shots=strategy_plan.summary.generated_video_shots,
        generated_image_shots=strategy_plan.summary.generated_image_shots,
        reused_video_shots=strategy_plan.summary.reused_video_shots,
        reused_image_shots=strategy_plan.summary.reused_image_shots,
        image_requirements=images,
        video_requirements=videos,
        image_requests=len(images),
        video_requests=len(videos),
        purchased_video_seconds=purchased_seconds,
        estimated_image_cost_per_request_usd=(
            authorization.estimated_image_cost_per_request_usd
        ),
        video_price_per_second_usd=authorization.video_price_per_second_usd,
        estimated_image_cost_usd=image_cost,
        estimated_video_cost_usd=video_cost,
        estimated_total_visual_cost_usd=total_cost,
        maximum_image_requests=authorization.maximum_image_requests,
        maximum_video_requests=authorization.maximum_video_requests,
        maximum_authorized_image_cost_usd=(
            authorization.maximum_authorized_image_cost_usd
        ),
        maximum_authorized_video_cost_per_request_usd=(
            authorization.maximum_authorized_video_cost_per_request_usd
        ),
        maximum_authorized_video_cost_usd=(
            authorization.maximum_authorized_video_cost_usd
        ),
        maximum_authorized_total_visual_cost_usd=(
            authorization.maximum_authorized_total_visual_cost_usd
        ),
        quality_floor_pass=quality_pass,
        quality_degradation_authorized=degradation,
        budget_pass=pass_value,
        fingerprint="0" * 64,
    )
    return AggregateVisualBudgetPlan(
        job_id=strategy_plan.job_id,
        source_strategy_fingerprint=strategy_plan.fingerprint,
        strategy=strategy_plan.strategy_name,
        visual_shot_count=strategy_plan.summary.visual_shot_count,
        generated_video_shots=strategy_plan.summary.generated_video_shots,
        generated_image_shots=strategy_plan.summary.generated_image_shots,
        reused_video_shots=strategy_plan.summary.reused_video_shots,
        reused_image_shots=strategy_plan.summary.reused_image_shots,
        image_requirements=images,
        video_requirements=videos,
        image_requests=len(images),
        video_requests=len(videos),
        purchased_video_seconds=purchased_seconds,
        estimated_image_cost_per_request_usd=(
            authorization.estimated_image_cost_per_request_usd
        ),
        video_price_per_second_usd=authorization.video_price_per_second_usd,
        estimated_image_cost_usd=image_cost,
        estimated_video_cost_usd=video_cost,
        estimated_total_visual_cost_usd=total_cost,
        maximum_image_requests=authorization.maximum_image_requests,
        maximum_video_requests=authorization.maximum_video_requests,
        maximum_authorized_image_cost_usd=(
            authorization.maximum_authorized_image_cost_usd
        ),
        maximum_authorized_video_cost_per_request_usd=(
            authorization.maximum_authorized_video_cost_per_request_usd
        ),
        maximum_authorized_video_cost_usd=(
            authorization.maximum_authorized_video_cost_usd
        ),
        maximum_authorized_total_visual_cost_usd=(
            authorization.maximum_authorized_total_visual_cost_usd
        ),
        quality_floor_pass=quality_pass,
        quality_degradation_authorized=degradation,
        budget_pass=pass_value,
        fingerprint=provisional.calculated_fingerprint(),
    )


def authorize_aggregate_visual_budget(
    plan: AggregateVisualBudgetPlan,
) -> AggregateVisualBudgetPlan:
    if not plan.budget_pass:
        raise AggregateVisualBudgetError(plan)
    return plan


def serialize_aggregate_visual_budget_plan(plan: AggregateVisualBudgetPlan) -> bytes:
    return _canonical_json(plan.model_dump(mode="json"))


def deserialize_aggregate_visual_budget_plan(content: bytes) -> AggregateVisualBudgetPlan:
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
        raise ValueError("aggregate visual budget must be a JSON object")
    return AggregateVisualBudgetPlan.model_validate(value)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


__all__ = [
    "AggregateVisualBudgetError",
    "AggregateVisualBudgetPlan",
    "HybridVisualBudgetAuthorization",
    "ImageRequirementKind",
    "PlannedImageRequest",
    "PlannedVideoRequest",
    "authorize_aggregate_visual_budget",
    "build_aggregate_visual_budget_plan",
    "deserialize_aggregate_visual_budget_plan",
    "serialize_aggregate_visual_budget_plan",
]
