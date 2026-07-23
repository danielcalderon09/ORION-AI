"""Canonical visual asset plan serialization."""

import json

from backend.src.production.visual_asset_planning.models import (
    ProductionVisualAssetPlan,
)


def serialize_visual_asset_plan(plan: ProductionVisualAssetPlan) -> bytes:
    return json.dumps(
        plan.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
