"""Closed simulated animation recipe; it never consumes creative prompts."""

import hashlib
import re

from backend.src.production.video_clip_generation.ports import VideoClipProviderRequest
from backend.src.production.video_clip_generation.providers.openrouter_models import (
    VideoMotionPrompt,
)


class VideoClipAnimationRecipeBuilder:
    """Derive a small deterministic motion recipe from a visual asset ID."""

    version = "simulated-motion-v1"

    def build(self, visual_asset_id: str) -> dict[str, str | bool]:
        digest = hashlib.sha256(visual_asset_id.encode("utf-8")).hexdigest()
        return {
            "version": self.version,
            "motion": "horizontal_geometric_overlay",
            "direction": "right" if int(digest[:2], 16) % 2 else "left",
            "deterministic": True,
        }


class VideoMotionPromptBuilder:
    """Build a closed prompt only from allowlisted durable source metadata."""

    version = "openrouter-motion-v1"

    def __init__(self, *, max_characters: int = 1200) -> None:
        if not 200 <= max_characters <= 2000:
            raise ValueError("motion prompt limit is outside safe bounds")
        self._maximum = max_characters

    def build(self, source: VideoClipProviderRequest) -> VideoMotionPrompt:
        role = _clean_fragment(source.source_role, maximum=80)
        subject = _metadata_fragment(source, "video_visual_subject", maximum=240)
        environment = _metadata_fragment(source, "video_environment", maximum=240)
        action = _metadata_fragment(source, "video_action", maximum=240)
        movement = _metadata_fragment(source, "video_camera_movement", maximum=80)
        framing = _metadata_fragment(source, "video_camera_framing", maximum=80)
        context = " ".join(
            fragment
            for fragment in (
                f"Subject: {subject}." if subject else "",
                f"Environment: {environment}." if environment else "",
                f"Motion intent: {action}." if action else "",
                f"Camera movement: {movement}." if movement else "",
                f"Framing: {framing}." if framing else "",
            )
            if fragment
        )
        text = (
            "Animate only the provided first frame. "
            f"Preserve the {role} subject identity, composition, colors, lighting, "
            f"and environment. {context} "
            "Use subtle natural subject motion. Keep motion physically coherent. "
            "Do not introduce new subjects, text, logos, cuts, transitions, flicker, "
            "warping, or changes of scene. Generate no audio."
        )
        text = text[: self._maximum].rstrip()
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return VideoMotionPrompt(text=text, sha256=digest, version=self.version)


_URL_OR_PATH = re.compile(
    r"(?:https?://\S+|file://\S+|[A-Za-z]:[\\/]\S+|(?:^|\s)/(?:[^\s/]+/)+\S+)",
    re.IGNORECASE,
)


def _clean_fragment(value: str, *, maximum: int) -> str:
    clean = "".join(character for character in value if ord(character) >= 32)
    clean = _URL_OR_PATH.sub(" ", clean)
    clean = " ".join(clean.split())
    return (clean[:maximum] or "visual").strip()


def _metadata_fragment(
    source: VideoClipProviderRequest, key: str, *, maximum: int
) -> str | None:
    value = source.source_metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return _clean_fragment(value, maximum=maximum)
