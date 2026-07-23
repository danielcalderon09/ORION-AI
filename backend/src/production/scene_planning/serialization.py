"""Canonical scene-plan serialization."""

import json

from backend.src.production.scene_planning.models import ProductionScenePlan


def serialize_scene_plan(plan: ProductionScenePlan) -> bytes:
    return json.dumps(
        plan.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
