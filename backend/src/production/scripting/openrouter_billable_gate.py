"""Fail-closed authorization for one durable OpenRouter scripting submission."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, field_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.scripting.exceptions import (
    ScriptingProviderConfigurationError,
)
from backend.src.production.scripting.openrouter_request import (
    OpenRouterScriptingRequestRecord,
    OpenRouterScriptingRequestStatus,
)


class OpenRouterScriptingBillablePolicy(ContractModel):
    provider: Literal["openrouter"] = "openrouter"
    allow_billable_requests: bool = False
    estimated_cost_usd: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=24,
        decimal_places=9,
    )
    maximum_authorized_cost_usd: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=24,
        decimal_places=9,
    )

    @field_validator(
        "estimated_cost_usd",
        "maximum_authorized_cost_usd",
        mode="before",
    )
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("OpenRouter scripting authorization must not use float")
        return value


class OpenRouterScriptingBillableGate:
    def authorize(
        self,
        *,
        policy: OpenRouterScriptingBillablePolicy,
        record: OpenRouterScriptingRequestRecord,
        unresolved_uncertain_submission: bool,
    ) -> None:
        if not policy.allow_billable_requests:
            raise ScriptingProviderConfigurationError(
                "OpenRouter scripting billable requests are disabled"
            )
        if policy.estimated_cost_usd is None:
            raise ScriptingProviderConfigurationError(
                "OpenRouter scripting cost estimate is required"
            )
        if policy.maximum_authorized_cost_usd is None:
            raise ScriptingProviderConfigurationError(
                "OpenRouter scripting cost authorization is required"
            )
        if policy.estimated_cost_usd > policy.maximum_authorized_cost_usd:
            raise ScriptingProviderConfigurationError(
                "OpenRouter scripting estimate exceeds authorization"
            )
        if (
            record.estimated_cost_usd != policy.estimated_cost_usd
            or record.maximum_authorized_cost_usd != policy.maximum_authorized_cost_usd
        ):
            raise ScriptingProviderConfigurationError(
                "OpenRouter scripting durable cost policy differs"
            )
        if record.status is not OpenRouterScriptingRequestStatus.PREPARED:
            raise ScriptingProviderConfigurationError(
                "OpenRouter scripting durable PREPARED checkpoint is required"
            )
        if not record.fresh_submission_permitted:
            raise ScriptingProviderConfigurationError(
                "OpenRouter scripting request forbids fresh submission"
            )
        if unresolved_uncertain_submission:
            raise ScriptingProviderConfigurationError(
                "uncertain OpenRouter scripting submission requires manual resolution"
            )


__all__ = [
    "OpenRouterScriptingBillableGate",
    "OpenRouterScriptingBillablePolicy",
]
