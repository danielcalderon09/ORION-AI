"""Canonical serialization for stable planning checksums."""

import json

from backend.src.production.planning.models import ProductionPlan


def serialize_production_plan(plan: ProductionPlan) -> bytes:
    return json.dumps(
        plan.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
