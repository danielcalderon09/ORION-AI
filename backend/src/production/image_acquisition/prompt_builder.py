"""Pure deterministic prompt construction from one approved visual spec."""

import hashlib

from pydantic import Field

from backend.src.production.domain.base import ContractModel
from backend.src.production.image_acquisition.exceptions import (
    ImageAcquisitionValidationError,
)
from backend.src.production.image_acquisition.ports import (
    ImageAcquisitionProviderRequest,
)


class BuiltImageGenerationPrompt(ContractModel):
    version: str
    text: str = Field(repr=False, min_length=1)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    visual_asset_id: str


class ImageGenerationPromptBuilder:
    prompt_version = "1.0.0"

    def __init__(self, *, max_prompt_bytes: int = 24_000) -> None:
        if max_prompt_bytes < 1:
            raise ValueError("maximum image prompt size must be positive")
        self._max_bytes = max_prompt_bytes

    def build(
        self,
        request: ImageAcquisitionProviderRequest,
    ) -> BuiltImageGenerationPrompt:
        asset = request.visual_asset
        composition = asset.composition
        camera = asset.camera_intent
        sections = (
            f"Visual subject: {asset.visual_subject}",
            f"Environment: {asset.environment}",
            (
                "Composition: "
                f"{composition.layout}; focal point {composition.focal_point}; "
                f"depth {composition.depth}; action {composition.action}"
            ),
            (
                "Camera intent: "
                f"{camera.framing}, {camera.angle}, {camera.movement}, "
                f"{camera.lens_millimeters}mm, subject {camera.subject}"
            ),
            f"Lighting: {asset.lighting}",
            f"Color direction: {asset.color_direction}",
            f"Style direction: {asset.style_direction}",
            f"Continuity group: {asset.continuity_group}",
            f"Approved visual instruction: {asset.prompt}",
            (
                f"Avoid: {asset.negative_prompt}"
                if asset.negative_prompt is not None
                else "Avoid: no additional negative instruction"
            ),
            (
                "Safety constraints: " + "; ".join(asset.safety_notes)
                if asset.safety_notes
                else "Safety constraints: safe visual content only"
            ),
            (
                f"Output composition must preserve aspect ratio {asset.aspect_ratio} "
                f"at {asset.width}x{asset.height}. Generate only one raster image."
            ),
        )
        text = "\n".join(sections)
        content = text.encode("utf-8")
        if len(content) > self._max_bytes:
            raise ImageAcquisitionValidationError(
                "image generation prompt exceeds the configured limit"
            )
        return BuiltImageGenerationPrompt(
            version=self.prompt_version,
            text=text,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            visual_asset_id=asset.asset_id,
        )
