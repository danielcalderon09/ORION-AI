"""Closed simulated animation recipe; it never consumes creative prompts."""

import hashlib


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
