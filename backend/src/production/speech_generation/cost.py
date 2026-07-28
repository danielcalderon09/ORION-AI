"""Provider-neutral Decimal speech pricing and cost estimation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.speech_generation.exceptions import (
    SpeechCostEstimationError,
)
from backend.src.production.speech_generation.remote_capabilities import (
    SpeechPricingCapability,
    SpeechPricingUnit,
)
from backend.src.production.speech_generation.voice_selection import (
    SpeechVoiceSelection,
)


class SpeechCostConfidence(StrEnum):
    EXACT = "exact"
    BOUNDED = "bounded"
    UNKNOWN = "unknown"


class SpeechCostAuthorizationStatus(StrEnum):
    AUTHORIZED = "authorized"
    REJECTED = "rejected"


class SpeechPricingSnapshot(ContractModel):
    schema_version: str = "1.0.0"
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=300)
    voice: str = Field(min_length=1, max_length=200)
    pricing: SpeechPricingCapability
    snapshot_at: datetime
    source: str = Field(min_length=1, max_length=100)

    @field_validator("snapshot_at")
    @classmethod
    def aware_snapshot(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("speech pricing snapshot time must be timezone-aware")
        return value

    def snapshot_hash(self) -> str:
        raw = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


class SpeechUsageMeasurement(ContractModel):
    normalized_characters: int = Field(gt=0, le=5_000_000)
    utf8_bytes: int = Field(gt=0, le=20_000_000)
    estimated_tokens: int | None = Field(default=None, gt=0, le=5_000_000)
    estimated_duration_ms: int = Field(gt=0, le=7_200_000)
    requests: int = Field(default=1, ge=1, le=1000)


class SpeechCostEstimate(ContractModel):
    schema_version: str = "1.0.0"
    provider: str
    model: str
    voice: str
    currency: str = Field(min_length=3, max_length=3)
    pricing_unit: SpeechPricingUnit
    estimated_billable_quantity: Decimal = Field(ge=0, max_digits=24, decimal_places=9)
    estimated_minimum_cost: Decimal = Field(ge=0, max_digits=24, decimal_places=9)
    estimated_maximum_cost: Decimal = Field(ge=0, max_digits=24, decimal_places=9)
    pricing_snapshot_at: datetime
    pricing_snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    calculation_method: str = Field(min_length=1, max_length=200)
    assumptions: tuple[str, ...] = Field(default=(), max_length=30)
    confidence: SpeechCostConfidence

    @field_validator(
        "estimated_billable_quantity",
        "estimated_minimum_cost",
        "estimated_maximum_cost",
        mode="before",
    )
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("speech cost estimates must not use float")
        return value

    @field_validator("pricing_snapshot_at")
    @classmethod
    def aware_pricing_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("speech estimate pricing time must be timezone-aware")
        return value

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value: str) -> str:
        normalized = value.upper()
        if not normalized.isalpha():
            raise ValueError("speech estimate currency is invalid")
        return normalized

    @model_validator(mode="after")
    def ordered_range(self) -> SpeechCostEstimate:
        if self.estimated_minimum_cost > self.estimated_maximum_cost:
            raise ValueError("speech cost estimate range is reversed")
        return self


class SpeechCostAuthorization(ContractModel):
    currency: str = Field(min_length=3, max_length=3)
    maximum_authorized_cost: Decimal = Field(gt=0, max_digits=24, decimal_places=9)
    status: SpeechCostAuthorizationStatus
    authorized_at: datetime
    authorization_reference: str = Field(min_length=1, max_length=200)

    @field_validator("maximum_authorized_cost", mode="before")
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("speech cost authorization must not use float")
        return value

    @field_validator("authorized_at")
    @classmethod
    def aware_authorized_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("speech cost authorization time must be timezone-aware")
        return value

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value: str) -> str:
        normalized = value.upper()
        if not normalized.isalpha():
            raise ValueError("speech authorization currency is invalid")
        return normalized


class SpeechReportedCost(ContractModel):
    currency: str = Field(min_length=3, max_length=3)
    amount: Decimal = Field(ge=0, max_digits=24, decimal_places=9)
    measured_quantity: Decimal | None = Field(default=None, ge=0, max_digits=24, decimal_places=9)

    @field_validator("amount", "measured_quantity", mode="before")
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("reported speech cost must not use float")
        return value

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value: str) -> str:
        normalized = value.upper()
        if not normalized.isalpha():
            raise ValueError("reported speech currency is invalid")
        return normalized


class SpeechCostEstimator:
    def estimate(
        self,
        *,
        selection: SpeechVoiceSelection,
        pricing: SpeechPricingSnapshot,
        usage: SpeechUsageMeasurement,
    ) -> SpeechCostEstimate:
        if (
            pricing.provider != selection.provider
            or pricing.model != selection.model
            or pricing.voice != selection.voice
        ):
            raise SpeechCostEstimationError("speech pricing identity does not match selection")
        capability = pricing.pricing
        if capability.pricing_unit is SpeechPricingUnit.UNKNOWN:
            raise SpeechCostEstimationError("speech pricing is unknown")
        usage_unit = (
            capability.usage_unit
            if capability.pricing_unit is SpeechPricingUnit.FIXED_PLUS_USAGE
            else capability.pricing_unit
        )
        if usage_unit is None:
            raise SpeechCostEstimationError("speech pricing usage unit is missing")
        quantity = _quantity(usage_unit, usage)
        if capability.minimum_unit_price is None or capability.maximum_unit_price is None:
            raise SpeechCostEstimationError("speech pricing range is incomplete")
        minimum = capability.fixed_base_cost + (quantity * capability.minimum_unit_price)
        maximum = capability.fixed_base_cost + (quantity * capability.maximum_unit_price)
        confidence = (
            SpeechCostConfidence.EXACT if minimum == maximum else SpeechCostConfidence.BOUNDED
        )
        return SpeechCostEstimate(
            provider=selection.provider,
            model=selection.model,
            voice=selection.voice,
            currency=capability.currency,
            pricing_unit=capability.pricing_unit,
            estimated_billable_quantity=quantity,
            estimated_minimum_cost=minimum,
            estimated_maximum_cost=maximum,
            pricing_snapshot_at=pricing.snapshot_at,
            pricing_snapshot_hash=pricing.snapshot_hash(),
            calculation_method=(f"{capability.pricing_unit.value}:{usage_unit.value}"),
            assumptions=capability.assumptions,
            confidence=confidence,
        )


def _quantity(
    unit: SpeechPricingUnit,
    usage: SpeechUsageMeasurement,
) -> Decimal:
    if unit is SpeechPricingUnit.PER_CHARACTER:
        return Decimal(usage.normalized_characters)
    if unit is SpeechPricingUnit.PER_BYTE:
        return Decimal(usage.utf8_bytes)
    if unit is SpeechPricingUnit.PER_TOKEN:
        if usage.estimated_tokens is None:
            raise SpeechCostEstimationError("speech token estimate is unavailable")
        return Decimal(usage.estimated_tokens)
    if unit is SpeechPricingUnit.PER_SECOND:
        return Decimal(usage.estimated_duration_ms) / Decimal(1000)
    if unit is SpeechPricingUnit.PER_MINUTE:
        return Decimal(usage.estimated_duration_ms) / Decimal(60_000)
    if unit is SpeechPricingUnit.PER_REQUEST:
        return Decimal(usage.requests)
    raise SpeechCostEstimationError("speech pricing usage unit is unsupported")
