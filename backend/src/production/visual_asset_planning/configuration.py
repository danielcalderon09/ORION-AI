"""Public provider-independent Visual Asset Planning configuration."""

import math
from typing import Literal

from pydantic import Field, model_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.visual_asset_planning.models import AssetKind

ContinuityStrength = Literal["low", "medium", "high"]
PromptDetailLevel = Literal["concise", "balanced", "detailed"]


class VisualAssetPlanningConfiguration(ContractModel):
    preferred_asset_kind: AssetKind = AssetKind.STILL_IMAGE
    images_per_shot: int = Field(default=1, ge=1, le=4)
    allow_video_specs: bool = False
    allow_reference_assets: bool = True
    continuity_strength: ContinuityStrength = "high"
    prompt_detail_level: PromptDetailLevel = "balanced"
    negative_prompt_enabled: bool = True
    target_width: int = Field(default=1080, ge=64, le=8192)
    target_height: int = Field(default=1920, ge=64, le=8192)
    safe_content_only: bool = True

    @model_validator(mode="after")
    def validate_dimensions_and_kind(self) -> "VisualAssetPlanningConfiguration":
        if self.preferred_asset_kind is AssetKind.VIDEO_CLIP and not self.allow_video_specs:
            raise ValueError("video asset kind requires allow_video_specs")
        _ = self.aspect_ratio
        return self

    @property
    def aspect_ratio(self) -> str:
        actual = self.target_width / self.target_height
        candidates = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0}
        for label, expected in candidates.items():
            if math.isclose(actual, expected, rel_tol=0.01):
                return label
        raise ValueError("target dimensions must use 16:9, 9:16, or 1:1")


def visual_asset_planning_configuration_from_snapshot(
    snapshot: dict[str, object],
) -> VisualAssetPlanningConfiguration:
    """Read nested configuration while accepting compatible historical flat jobs."""

    raw = snapshot.get("configuration", {})
    if not isinstance(raw, dict):
        return VisualAssetPlanningConfiguration.model_validate(raw)
    nested = raw.get("visual_asset_planning")
    if nested is not None:
        return VisualAssetPlanningConfiguration.model_validate(nested)
    compatible = {
        key: value
        for key, value in raw.items()
        if key in VisualAssetPlanningConfiguration.model_fields
    }
    return VisualAssetPlanningConfiguration.model_validate(compatible)
