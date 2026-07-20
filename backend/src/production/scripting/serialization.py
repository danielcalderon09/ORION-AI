"""Canonical serialization for stable SCRIPTING checksums."""

import json

from backend.src.production.scripting.models import ProductionScript


def serialize_production_script(script: ProductionScript) -> bytes:
    return json.dumps(
        script.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
