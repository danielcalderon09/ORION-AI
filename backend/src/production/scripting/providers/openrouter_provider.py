"""Controlled OpenRouter scripting adapter with durable billable checkpoints."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from time import monotonic

import httpx
from pydantic import ValidationError

from backend.src.production.infrastructure.openai_compatible import (
    OpenAICompatibleAuthenticationError,
    OpenAICompatibleProtocolError,
    OpenAICompatibleRateLimitError,
    OpenAICompatibleResponsesClient,
    OpenAICompatibleTimeoutError,
    OpenAICompatibleUnavailableError,
    Sleeper,
    load_strict_json_object,
)
from backend.src.production.scripting.duration_policy import (
    validate_openrouter_duration_policy,
)
from backend.src.production.scripting.exceptions import (
    ScriptingProviderAuthenticationError,
    ScriptingProviderConfigurationError,
    ScriptingProviderContractError,
    ScriptingProviderRateLimitError,
    ScriptingProviderResponseError,
    ScriptingProviderTimeoutError,
    ScriptingProviderUnavailableError,
    ScriptingProviderUncertainError,
)
from backend.src.production.scripting.models import (
    ProductionScript,
    validate_script_against_plan,
)
from backend.src.production.scripting.openrouter_billable_gate import (
    OpenRouterScriptingBillableGate,
    OpenRouterScriptingBillablePolicy,
)
from backend.src.production.scripting.openrouter_request import (
    OpenRouterScriptingFingerprintInput,
    OpenRouterScriptingRequestRecord,
    OpenRouterScriptingRequestStatus,
    openrouter_scripting_request_fingerprint,
    openrouter_scripting_request_relative_path,
    scripting_configuration_fingerprint,
)
from backend.src.production.scripting.openrouter_request_store import (
    OpenRouterScriptingRequestConflictError,
    OpenRouterScriptingRequestStore,
    OpenRouterScriptingRequestStoreError,
)
from backend.src.production.scripting.ports import (
    ScriptingProviderRequest,
    ScriptingProviderResponse,
)
from backend.src.production.scripting.prompt_builder import ScriptingPromptBuilder
from backend.src.production.scripting.serialization import serialize_production_script


class OpenRouterScriptingProvider:
    """Submit at most once after a durable checkpoint and strict local validation."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        prompt_builder: ScriptingPromptBuilder,
        request_store: OpenRouterScriptingRequestStore,
        billable_policy: OpenRouterScriptingBillablePolicy,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout_seconds: float = 120,
        max_transport_attempts: int = 1,
        retry_base_delay_seconds: float = 0.25,
        max_output_tokens: int = 8192,
        temperature: float = 0.2,
        max_response_bytes: int = 2_000_000,
        http_referer: str | None = None,
        app_title: str | None = None,
        client: httpx.AsyncClient | None = None,
        owns_client: bool = False,
        sleeper: Sleeper = asyncio.sleep,
        monotonic_clock: Callable[[], float] = monotonic,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        gate: OpenRouterScriptingBillableGate | None = None,
    ) -> None:
        if not api_key.strip():
            raise ScriptingProviderConfigurationError("scripting provider credential is missing")
        if not model.strip():
            raise ScriptingProviderConfigurationError("scripting model is missing")
        if max_transport_attempts != 1:
            raise ScriptingProviderConfigurationError(
                "OpenRouter scripting does not permit automatic retries"
            )
        if timeout_seconds <= 0 or retry_base_delay_seconds <= 0:
            raise ScriptingProviderConfigurationError(
                "scripting timeout and retry delay must be positive"
            )
        if max_output_tokens < 1 or not 0 <= temperature <= 2:
            raise ScriptingProviderConfigurationError(
                "scripting output token or temperature setting is invalid"
            )
        if not billable_policy.allow_billable_requests:
            raise ScriptingProviderConfigurationError(
                "OpenRouter scripting billable requests are disabled"
            )
        if (
            billable_policy.estimated_cost_usd is None
            or billable_policy.maximum_authorized_cost_usd is None
        ):
            raise ScriptingProviderConfigurationError(
                "OpenRouter scripting cost estimate and authorization are required"
            )
        try:
            self._transport = OpenAICompatibleResponsesClient(
                api_key=api_key,
                base_url=base_url,
                timeout_seconds=timeout_seconds,
                max_transport_attempts=1,
                retry_base_delay_seconds=retry_base_delay_seconds,
                http_referer=http_referer,
                app_title=app_title,
                client=client,
                owns_client=owns_client,
                max_response_bytes=max_response_bytes,
                sleeper=sleeper,
            )
        except ValueError as exc:
            raise ScriptingProviderConfigurationError(
                "scripting provider HTTP configuration is invalid"
            ) from exc
        self._model = model.strip()
        self._prompt_builder = prompt_builder
        self._store = request_store
        self._policy = billable_policy
        self._gate = gate or OpenRouterScriptingBillableGate()
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._monotonic = monotonic_clock
        self._clock = clock

    async def generate_script(self, request: ScriptingProviderRequest) -> ScriptingProviderResponse:
        try:
            prompt = self._prompt_builder.build(request)
            fingerprint_input = self._fingerprint_input(request)
            fingerprint = openrouter_scripting_request_fingerprint(fingerprint_input)
        except (TypeError, ValueError) as exc:
            raise ScriptingProviderContractError(
                "scripting prompt could not be constructed"
            ) from exc
        record, records = await self._load_or_prepare(
            request=request,
            fingerprint_input=fingerprint_input,
            fingerprint=fingerprint,
        )
        if record.status is OpenRouterScriptingRequestStatus.COMPLETED:
            return self._response_from_record(record, recovered=True)
        if record.status is OpenRouterScriptingRequestStatus.SUBMITTING:
            uncertain = record.model_copy(
                update={
                    "status": OpenRouterScriptingRequestStatus.UNCERTAIN,
                    "terminal_at": self._aware_now(),
                    "safe_error_code": "interrupted_submission",
                }
            )
            await self._checkpoint(record, uncertain)
            raise ScriptingProviderUncertainError(
                "OpenRouter scripting submission requires manual resolution"
            )
        if record.status is OpenRouterScriptingRequestStatus.UNCERTAIN:
            raise ScriptingProviderUncertainError(
                "OpenRouter scripting submission requires manual resolution"
            )
        if record.status is OpenRouterScriptingRequestStatus.FAILED:
            raise ScriptingProviderContractError(
                "failed OpenRouter scripting request cannot be retried automatically"
            )
        unresolved = any(
            item.request_fingerprint == fingerprint
            and item.attempt_number != record.attempt_number
            and item.status
            in {
                OpenRouterScriptingRequestStatus.SUBMITTING,
                OpenRouterScriptingRequestStatus.UNCERTAIN,
            }
            for item in records
        )
        self._gate.authorize(
            policy=self._policy,
            record=record,
            unresolved_uncertain_submission=unresolved,
        )
        submitting = record.model_copy(
            update={
                "status": OpenRouterScriptingRequestStatus.SUBMITTING,
                "submission_started_at": self._aware_now(),
                "fresh_submission_permitted": False,
            }
        )
        await self._checkpoint(record, submitting)
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "production_script",
                    "strict": True,
                    "schema": prompt.response_schema,
                },
            },
            "provider": {
                "require_parameters": True,
                "data_collection": "deny",
                "zdr": True,
            },
            "max_tokens": self._max_output_tokens,
            "temperature": self._temperature,
            "store": False,
            "stream": False,
        }
        started = self._monotonic()
        try:
            body, request_id = await self._transport.post(payload)
        except asyncio.CancelledError:
            await self._mark_uncertain(submitting, "cancelled_after_submission")
            raise
        except (OpenAICompatibleTimeoutError, OpenAICompatibleUnavailableError) as exc:
            await self._mark_uncertain(submitting, "ambiguous_transport_failure")
            mapped: type[Exception] = (
                ScriptingProviderTimeoutError
                if isinstance(exc, OpenAICompatibleTimeoutError)
                else ScriptingProviderUnavailableError
            )
            raise ScriptingProviderUncertainError(
                "OpenRouter scripting submission outcome is uncertain"
            ) from mapped("scripting provider transport failed")
        except OpenAICompatibleAuthenticationError as exc:
            await self._mark_failed(submitting, "authentication_rejected")
            raise ScriptingProviderAuthenticationError(
                "scripting provider rejected authentication"
            ) from exc
        except OpenAICompatibleRateLimitError as exc:
            await self._mark_failed(submitting, "rate_limited")
            raise ScriptingProviderRateLimitError("scripting provider rate limit reached") from exc
        except OpenAICompatibleProtocolError as exc:
            await self._mark_failed(submitting, "provider_protocol_error")
            raise ScriptingProviderResponseError(
                "scripting provider returned an invalid response"
            ) from exc
        latency_ms = max(0.0, (self._monotonic() - started) * 1000)
        try:
            if body.get("error") is not None:
                raise ValueError("provider response contains an error")
            text = self._transport.extract_single_output_text(body)
            script = ProductionScript.model_validate(load_strict_json_object(text))
            if script.schema_version != "1.0.0":
                raise ValueError("unsupported production script schema version")
            validate_script_against_plan(script, request.plan)
            assessment = validate_openrouter_duration_policy(
                script,
                reading_speed_words_per_minute=(
                    request.configuration.reading_speed_words_per_minute
                ),
            )
        except (ValueError, ValidationError, TypeError, OpenAICompatibleProtocolError) as exc:
            await self._mark_failed(submitting, "invalid_structured_output")
            raise ScriptingProviderContractError(
                "scripting provider output failed contract validation"
            ) from exc
        usage_value = body.get("usage")
        usage = usage_value if isinstance(usage_value, dict) else {}
        reported_model = self._transport.safe_string(body.get("model"))
        reported_cost = _safe_decimal(usage.get("cost"))
        completed = submitting.model_copy(
            update={
                "status": OpenRouterScriptingRequestStatus.COMPLETED,
                "terminal_at": self._aware_now(),
                "provider_request_id": request_id,
                "reported_model": reported_model,
                "input_tokens": self._transport.safe_int(
                    usage.get("prompt_tokens", usage.get("input_tokens"))
                ),
                "output_tokens": self._transport.safe_int(
                    usage.get("completion_tokens", usage.get("output_tokens"))
                ),
                "total_tokens": self._transport.safe_int(usage.get("total_tokens")),
                "reported_cost_usd": reported_cost,
                "finish_reason": self._transport.extract_finish_reason(body),
                "script_sha256": hashlib.sha256(serialize_production_script(script)).hexdigest(),
                "script": script,
                "metadata": {
                    "duration_policy": "bounded_words_v1",
                    "narration_word_count": assessment.narration_word_count,
                },
            }
        )
        await self._checkpoint(submitting, completed)
        return self._response_from_record(completed, latency_ms=latency_ms)

    async def close(self) -> None:
        await self._transport.close()

    async def _load_or_prepare(
        self,
        *,
        request: ScriptingProviderRequest,
        fingerprint_input: OpenRouterScriptingFingerprintInput,
        fingerprint: str,
    ) -> tuple[
        OpenRouterScriptingRequestRecord,
        tuple[OpenRouterScriptingRequestRecord, ...],
    ]:
        try:
            records = await self._store.list_for_job(job_id=request.job_id)
            current = await self._store.read(
                job_id=request.job_id,
                attempt_number=request.attempt_number,
            )
        except OpenRouterScriptingRequestStoreError as exc:
            raise ScriptingProviderContractError(
                "OpenRouter scripting request state is unreadable"
            ) from exc
        completed = next(
            (
                item
                for item in records
                if item.request_fingerprint == fingerprint
                and item.status is OpenRouterScriptingRequestStatus.COMPLETED
            ),
            None,
        )
        if current is None and completed is not None:
            return completed, records
        if current is not None:
            if (
                current.request_fingerprint != fingerprint
                or current.fingerprint_input != fingerprint_input
            ):
                raise ScriptingProviderContractError(
                    "OpenRouter scripting request conflicts with durable state"
                )
            return current, records
        assert self._policy.estimated_cost_usd is not None
        assert self._policy.maximum_authorized_cost_usd is not None
        prepared = OpenRouterScriptingRequestRecord(
            job_id=request.job_id,
            attempt_number=request.attempt_number,
            fingerprint_input=fingerprint_input,
            request_fingerprint=fingerprint,
            status=OpenRouterScriptingRequestStatus.PREPARED,
            estimated_cost_usd=self._policy.estimated_cost_usd,
            maximum_authorized_cost_usd=self._policy.maximum_authorized_cost_usd,
            prepared_at=self._aware_now(),
            fresh_submission_permitted=True,
            metadata={"raw_response_persisted": False},
        )
        try:
            await self._store.create(prepared)
        except OpenRouterScriptingRequestStoreError as exc:
            raise ScriptingProviderContractError(
                "OpenRouter scripting request could not be checkpointed"
            ) from exc
        return prepared, (*records, prepared)

    def _fingerprint_input(
        self,
        request: ScriptingProviderRequest,
    ) -> OpenRouterScriptingFingerprintInput:
        return OpenRouterScriptingFingerprintInput(
            model=self._model,
            source_prompt_sha256=request.source_prompt_sha256,
            source_plan_artifact_id=request.source_plan_artifact_id,
            source_plan_sha256=request.source_plan_sha256,
            language=request.language,
            target_duration_seconds=request.target_duration_seconds,
            aspect_ratio=request.plan.aspect_ratio,
            scene_count=len(request.plan.scenes),
            scripting_configuration_sha256=scripting_configuration_fingerprint(
                request.configuration.model_dump(mode="json")
            ),
            prompt_template_version=self._prompt_builder.scripting_prompt_version,
            prompt_template_sha256=self._prompt_builder.template_fingerprint(),
            temperature=Decimal(str(self._temperature)),
            max_output_tokens=self._max_output_tokens,
        )

    async def _mark_uncertain(
        self,
        record: OpenRouterScriptingRequestRecord,
        code: str,
    ) -> None:
        await self._checkpoint(
            record,
            record.model_copy(
                update={
                    "status": OpenRouterScriptingRequestStatus.UNCERTAIN,
                    "terminal_at": self._aware_now(),
                    "safe_error_code": code,
                }
            ),
        )

    async def _mark_failed(
        self,
        record: OpenRouterScriptingRequestRecord,
        code: str,
    ) -> None:
        await self._checkpoint(
            record,
            record.model_copy(
                update={
                    "status": OpenRouterScriptingRequestStatus.FAILED,
                    "terminal_at": self._aware_now(),
                    "safe_error_code": code,
                }
            ),
        )

    async def _checkpoint(
        self,
        previous: OpenRouterScriptingRequestRecord,
        current: OpenRouterScriptingRequestRecord,
    ) -> None:
        try:
            await self._store.checkpoint(previous=previous, current=current)
        except OpenRouterScriptingRequestConflictError as exc:
            raise ScriptingProviderUncertainError(
                "OpenRouter scripting checkpoint changed concurrently"
            ) from exc
        except OpenRouterScriptingRequestStoreError as exc:
            raise ScriptingProviderUncertainError(
                "OpenRouter scripting checkpoint could not be persisted"
            ) from exc

    def _response_from_record(
        self,
        record: OpenRouterScriptingRequestRecord,
        *,
        latency_ms: float = 0,
        recovered: bool = False,
    ) -> ScriptingProviderResponse:
        if record.script is None:
            raise ScriptingProviderContractError(
                "completed OpenRouter scripting request has no validated script"
            )
        return ScriptingProviderResponse(
            script=record.script,
            provider="openrouter",
            model=record.reported_model or self._model,
            requested_model=self._model,
            reported_model=record.reported_model,
            request_id=record.provider_request_id,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            total_tokens=record.total_tokens,
            reported_cost_usd=record.reported_cost_usd,
            latency_ms=latency_ms,
            finish_reason=record.finish_reason,
            metadata={
                "request_fingerprint": record.request_fingerprint,
                "request_record_relative_path": (
                    openrouter_scripting_request_relative_path(record)
                ),
                "request_state": record.status.value,
                "recovered": recovered,
                "estimated_cost_usd": str(record.estimated_cost_usd),
                "maximum_authorized_cost_usd": str(record.maximum_authorized_cost_usd),
            },
        )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ScriptingProviderConfigurationError(
                "OpenRouter scripting clock must be timezone-aware"
            )
        return value


def _safe_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if not isinstance(value, (Decimal, int, float, str)):
        return None
    try:
        parsed = Decimal(str(value))
    except InvalidOperation:
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return parsed


__all__ = ["OpenRouterScriptingProvider"]
