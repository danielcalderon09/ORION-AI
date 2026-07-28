"""Fail-closed authorization gate for future billable speech submission."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, field_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.speech_generation.cost import (
    SpeechCostAuthorizationStatus,
)
from backend.src.production.speech_generation.exceptions import (
    SpeechBillableAuthorizationError,
)
from backend.src.production.speech_generation.remote_capabilities import (
    SpeechCapabilitySnapshot,
    SpeechPricingUnit,
)
from backend.src.production.speech_generation.remote_models import (
    RemoteSpeechJobRecord,
    RemoteSpeechJobStatus,
)


class SpeechBillableRequestPolicy(ContractModel):
    allow_billable_requests: bool = False
    remote_provider: str = Field(default="disabled", min_length=1, max_length=100)
    provider_configuration_valid: bool = False
    live_adapter_available: bool = False


class SpeechBillableRequestAuthorization(ContractModel):
    provider: str
    model: str
    voice: str
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    capability_snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    pricing_snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    currency: str
    estimated_maximum_cost: Decimal = Field(ge=0, max_digits=24, decimal_places=9)
    maximum_authorized_cost: Decimal = Field(gt=0, max_digits=24, decimal_places=9)
    durable_status: RemoteSpeechJobStatus
    authorized_at: datetime

    @field_validator(
        "estimated_maximum_cost",
        "maximum_authorized_cost",
        mode="before",
    )
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("billable speech authorization must not use float")
        return value

    @field_validator("authorized_at")
    @classmethod
    def aware_authorization(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("billable speech authorization time must be timezone-aware")
        return value


class SpeechBillableRequestGate:
    """Authorize only a durable PREPARED record with explicit safe inputs."""

    def authorize(
        self,
        *,
        policy: SpeechBillableRequestPolicy,
        record: RemoteSpeechJobRecord,
        capability_snapshot: SpeechCapabilitySnapshot | None,
        unresolved_uncertain_submission: bool,
        authorized_at: datetime,
    ) -> SpeechBillableRequestAuthorization:
        if record.provider in {"simulated", "orion-simulated-speech"}:
            raise SpeechBillableAuthorizationError(
                "simulated speech is not a billable remote provider"
            )
        if not policy.allow_billable_requests:
            raise SpeechBillableAuthorizationError("billable speech requests are disabled")
        if policy.remote_provider == "disabled":
            raise SpeechBillableAuthorizationError("remote speech provider is disabled")
        if policy.remote_provider != record.provider:
            raise SpeechBillableAuthorizationError("remote speech provider selection differs")
        if not policy.provider_configuration_valid or not policy.live_adapter_available:
            raise SpeechBillableAuthorizationError(
                "remote speech provider configuration is unavailable"
            )
        if capability_snapshot is None:
            raise SpeechBillableAuthorizationError("speech capability snapshot is required")
        if (
            capability_snapshot.capabilities.provider != record.provider
            or capability_snapshot.snapshot_hash() != record.capability_snapshot_hash
        ):
            raise SpeechBillableAuthorizationError(
                "speech capability snapshot does not match request"
            )
        if record.estimated_cost.pricing_unit is SpeechPricingUnit.UNKNOWN:
            raise SpeechBillableAuthorizationError("speech pricing is unknown")
        authorization = record.authorization
        if authorization is None:
            raise SpeechBillableAuthorizationError("explicit speech cost authorization is required")
        if authorization.status is not SpeechCostAuthorizationStatus.AUTHORIZED:
            raise SpeechBillableAuthorizationError("speech cost authorization was rejected")
        if (
            authorization.currency != record.estimated_cost.currency
            or authorization.maximum_authorized_cost < record.estimated_cost.estimated_maximum_cost
        ):
            raise SpeechBillableAuthorizationError("speech estimate exceeds cost authorization")
        if record.status is not RemoteSpeechJobStatus.PREPARED:
            raise SpeechBillableAuthorizationError("durable PREPARED checkpoint is required")
        if not record.fresh_submission_permitted:
            raise SpeechBillableAuthorizationError("remote speech record forbids fresh submission")
        if unresolved_uncertain_submission:
            raise SpeechBillableAuthorizationError(
                "uncertain prior speech submission requires manual resolution"
            )
        return SpeechBillableRequestAuthorization(
            provider=record.provider,
            model=record.model,
            voice=record.voice,
            request_fingerprint=record.request_fingerprint,
            capability_snapshot_hash=record.capability_snapshot_hash,
            pricing_snapshot_hash=record.pricing_snapshot_hash,
            currency=record.estimated_cost.currency,
            estimated_maximum_cost=record.estimated_cost.estimated_maximum_cost,
            maximum_authorized_cost=authorization.maximum_authorized_cost,
            durable_status=record.status,
            authorized_at=authorized_at,
        )
