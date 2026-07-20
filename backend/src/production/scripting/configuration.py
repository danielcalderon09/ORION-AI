"""Public, provider-independent SCRIPTING configuration."""

from typing import Literal

from pydantic import Field, field_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.planning.validation import validate_planning_text


class ScriptingConfiguration(ContractModel):
    tone: str = Field(default="engaging", min_length=1, max_length=100)
    narration_density: Literal["concise", "balanced", "detailed"] = "balanced"
    include_opening_hook: bool = True
    include_call_to_action: bool = False
    max_words_per_scene: int = Field(default=180, ge=10, le=500)
    reading_speed_words_per_minute: int = Field(default=150, ge=80, le=240)
    preserve_on_screen_text: bool = True

    @field_validator("tone")
    @classmethod
    def validate_tone(cls, value: str) -> str:
        return validate_planning_text(value)


def scripting_configuration_from_snapshot(snapshot: dict[str, object]) -> ScriptingConfiguration:
    """Read nested Phase 5B configuration while accepting historical flat jobs."""

    raw = snapshot.get("configuration", {})
    if not isinstance(raw, dict):
        return ScriptingConfiguration.model_validate(raw)
    nested = raw.get("scripting")
    if nested is not None:
        return ScriptingConfiguration.model_validate(nested)
    scripting_keys = set(ScriptingConfiguration.model_fields)
    compatible = {key: value for key, value in raw.items() if key in scripting_keys}
    return ScriptingConfiguration.model_validate(compatible)
