"""Strict global configuration exposed to provider requests without secrets."""

from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from backend.src.production.domain.base import ContractModel

ImageOutputFormat = Literal["png", "jpeg", "webp"]
ImageQuality = Literal["auto", "low", "medium", "high"]


class ImageAcquisitionConfiguration(ContractModel):
    output_format: ImageOutputFormat = "png"
    quality: ImageQuality = "auto"
    images_per_request: int = Field(default=1, ge=1, le=1)


class OpenRouterImageBillablePolicy(ContractModel):
    allow_billable_requests: bool = False
    estimated_cost_usd: Decimal | None = Field(
        default=None, gt=0, max_digits=18, decimal_places=9
    )
    maximum_authorized_cost_usd: Decimal | None = Field(
        default=None, gt=0, max_digits=18, decimal_places=9
    )
    maximum_requests_per_job: int = Field(default=1, ge=1, le=50)

    @field_validator("estimated_cost_usd", "maximum_authorized_cost_usd", mode="before")
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("image cost authorization must use Decimal text")
        return value

    @model_validator(mode="after")
    def authorize_bounded_cost(self) -> "OpenRouterImageBillablePolicy":
        if not self.allow_billable_requests:
            return self
        if self.estimated_cost_usd is None or self.maximum_authorized_cost_usd is None:
            raise ValueError("billable image requests require explicit cost authorization")
        if self.estimated_cost_usd > self.maximum_authorized_cost_usd:
            raise ValueError("image estimate exceeds authorized cost")
        return self
