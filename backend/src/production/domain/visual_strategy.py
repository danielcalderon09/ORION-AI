"""Provider-neutral visual strategy vocabulary for durable shot planning."""

from enum import StrEnum


class VisualMode(StrEnum):
    """How a visual shot is expected to obtain its visual source."""

    GENERATED_VIDEO = "generated_video"
    GENERATED_IMAGE = "generated_image"
    REUSED_VIDEO = "reused_video"
    REUSED_IMAGE = "reused_image"


class VisualMotionMode(StrEnum):
    """Local motion intent; rendering support is introduced in a later phase."""

    STATIC = "static"
    PAN = "pan"
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"
    PAN_AND_ZOOM = "pan_and_zoom"


class VisualImportance(StrEnum):
    """Bounded editorial impact used by future strategy selection."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    HERO = "hero"


class VisualGenerationPriority(StrEnum):
    """Stable generation ordering without accepting arbitrary LLM scores."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    REQUIRED = "required"


__all__ = [
    "VisualGenerationPriority",
    "VisualImportance",
    "VisualMode",
    "VisualMotionMode",
]
