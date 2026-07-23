"""Strict global configuration exposed to provider requests without secrets."""

from typing import Literal

from pydantic import Field

from backend.src.production.domain.base import ContractModel

ImageOutputFormat = Literal["png", "jpeg", "webp"]
ImageQuality = Literal["auto", "low", "medium", "high"]


class ImageAcquisitionConfiguration(ContractModel):
    output_format: ImageOutputFormat = "png"
    quality: ImageQuality = "auto"
    images_per_request: int = Field(default=1, ge=1, le=1)
