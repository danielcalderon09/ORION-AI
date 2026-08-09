"""Controlled OpenRouter scripting adapter with durable billable checkpoints."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from time import monotonic
from typing import NoReturn

import httpx
from pydantic import ValidationError

from backend.src.production.infrastructure.openai_compatible import (
    OpenAICompatibleAuthenticationError,
    OpenAICompatibleProtocolError,
    OpenAICompatibleProtocolErrorCode,
    OpenAICompatibleRateLimitError,
    OpenAICompatibleResponse,
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
    ensure_narrative_progression,
    validate_narration_repetition,
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
    OpenRouterScriptingValidationErrorCode,
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


@dataclass(frozen=True, slots=True)
class _SafeResponseMetadata:
    http_status: int | None = None
    provider_request_id: str | None = None
    reported_model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    reported_cost_usd: Decimal | None = None
    finish_reason: str | None = None


@dataclass(frozen=True, slots=True)
class _ValidationFailure:
    code: OpenRouterScriptingValidationErrorCode
    path: str | None
    message: str


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
            transport_response = await self._transport.post_with_metadata(payload)
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
            await self._mark_failed(
                submitting,
                "provider_protocol_error",
                response_metadata=_SafeResponseMetadata(
                    http_status=exc.status_code,
                    provider_request_id=_safe_provider_request_id(exc.request_id),
                ),
                validation_failure=_ValidationFailure(
                    code=OpenRouterScriptingValidationErrorCode.PROVIDER_ENVELOPE_PROTOCOL,
                    path=None,
                    message=_protocol_error_message(exc.diagnostic_code),
                ),
            )
            raise ScriptingProviderResponseError(
                "scripting provider returned an invalid response"
            ) from exc
        latency_ms = max(0.0, (self._monotonic() - started) * 1000)
        body = transport_response.body
        response_metadata = _safe_response_metadata(transport_response, self._transport)
        if body.get("error") is not None:
            await self._raise_structured_output_failure(
                submitting,
                response_metadata=response_metadata,
                failure=_ValidationFailure(
                    code=OpenRouterScriptingValidationErrorCode.PROVIDER_BODY_ERROR,
                    path="error",
                    message="provider returned an error object",
                ),
                cause=ValueError("provider response contains an error"),
            )
        try:
            text = self._transport.extract_single_output_text(body)
        except OpenAICompatibleProtocolError as exc:
            await self._raise_structured_output_failure(
                submitting,
                response_metadata=response_metadata,
                failure=_ValidationFailure(
                    code=OpenRouterScriptingValidationErrorCode.OUTPUT_TEXT_PROTOCOL,
                    path="choices[0].message.content",
                    message="provider response has no single non-empty output text",
                ),
                cause=exc,
            )
        if text.lstrip().startswith("```"):
            await self._raise_structured_output_failure(
                submitting,
                response_metadata=response_metadata,
                failure=_ValidationFailure(
                    code=OpenRouterScriptingValidationErrorCode.OUTPUT_TEXT_PROTOCOL,
                    path="choices[0].message.content",
                    message="Markdown wrappers are not allowed",
                ),
                cause=ValueError("Markdown wrapper is not valid structured output"),
            )
        try:
            script_payload = load_strict_json_object(text)
        except json.JSONDecodeError as exc:
            await self._raise_structured_output_failure(
                submitting,
                response_metadata=response_metadata,
                failure=_ValidationFailure(
                    code=OpenRouterScriptingValidationErrorCode.INNER_JSON_PARSE,
                    path="choices[0].message.content",
                    message="output text is not valid JSON",
                ),
                cause=exc,
            )
        except ValueError as exc:
            code = (
                OpenRouterScriptingValidationErrorCode.DUPLICATE_JSON_KEY
                if str(exc).startswith("duplicate JSON key:")
                else OpenRouterScriptingValidationErrorCode.INNER_JSON_PARSE
            )
            message = (
                "duplicate JSON keys are not allowed"
                if code is OpenRouterScriptingValidationErrorCode.DUPLICATE_JSON_KEY
                else "output text is not a valid JSON object"
            )
            await self._raise_structured_output_failure(
                submitting,
                response_metadata=response_metadata,
                failure=_ValidationFailure(
                    code=code,
                    path="choices[0].message.content",
                    message=message,
                ),
                cause=exc,
            )
        try:
            script = ProductionScript.model_validate(script_payload)
        except ValidationError as exc:
            await self._raise_structured_output_failure(
                submitting,
                response_metadata=response_metadata,
                failure=_pydantic_validation_failure(exc),
                cause=exc,
            )
        if script.schema_version != "1.0.0":
            await self._raise_structured_output_failure(
                submitting,
                response_metadata=response_metadata,
                failure=_ValidationFailure(
                    code=OpenRouterScriptingValidationErrorCode.PRODUCTION_SCRIPT_CONTRACT,
                    path="schema_version",
                    message="unsupported production script schema version",
                ),
                cause=ValueError("unsupported production script schema version"),
            )
        try:
            validate_script_against_plan(script, request.plan)
        except ValueError as exc:
            await self._raise_structured_output_failure(
                submitting,
                response_metadata=response_metadata,
                failure=_plan_contract_failure(exc),
                cause=exc,
            )
        try:
            assessment = validate_openrouter_duration_policy(
                script,
                reading_speed_words_per_minute=(
                    request.configuration.reading_speed_words_per_minute
                ),
            )
        except ValueError as exc:
            await self._raise_structured_output_failure(
                submitting,
                response_metadata=response_metadata,
                failure=_ValidationFailure(
                    code=OpenRouterScriptingValidationErrorCode.DURATION_POLICY,
                    path="scenes",
                    message=_duration_policy_message(exc),
                ),
                cause=exc,
            )
        try:
            validate_narration_repetition(script)
        except ValueError as exc:
            await self._raise_structured_output_failure(
                submitting,
                response_metadata=response_metadata,
                failure=_ValidationFailure(
                    code=OpenRouterScriptingValidationErrorCode.PRODUCTION_SCRIPT_CONTRACT,
                    path="scenes",
                    message="consecutive scenes repeat narration",
                ),
                cause=exc,
            )
        script = ensure_narrative_progression(script)
        completed = submitting.model_copy(
            update={
                "status": OpenRouterScriptingRequestStatus.COMPLETED,
                "terminal_at": self._aware_now(),
                "http_status": response_metadata.http_status,
                "provider_request_id": response_metadata.provider_request_id,
                "reported_model": response_metadata.reported_model,
                "input_tokens": response_metadata.input_tokens,
                "output_tokens": response_metadata.output_tokens,
                "total_tokens": response_metadata.total_tokens,
                "reported_cost_usd": response_metadata.reported_cost_usd,
                "finish_reason": response_metadata.finish_reason,
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
            requested_model=self._model,
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
        *,
        response_metadata: _SafeResponseMetadata | None = None,
        validation_failure: _ValidationFailure | None = None,
    ) -> None:
        update: dict[str, object] = {
            "status": OpenRouterScriptingRequestStatus.FAILED,
            "terminal_at": self._aware_now(),
            "safe_error_code": code,
        }
        if response_metadata is not None:
            update.update(
                {
                    "http_status": response_metadata.http_status,
                    "provider_request_id": response_metadata.provider_request_id,
                    "reported_model": response_metadata.reported_model,
                    "input_tokens": response_metadata.input_tokens,
                    "output_tokens": response_metadata.output_tokens,
                    "total_tokens": response_metadata.total_tokens,
                    "reported_cost_usd": response_metadata.reported_cost_usd,
                    "finish_reason": response_metadata.finish_reason,
                }
            )
        if validation_failure is not None:
            update.update(
                {
                    "validation_error_code": validation_failure.code,
                    "validation_error_path": validation_failure.path,
                    "validation_error_message": validation_failure.message,
                }
            )
        await self._checkpoint(
            record,
            record.model_copy(update=update),
        )

    async def _raise_structured_output_failure(
        self,
        record: OpenRouterScriptingRequestRecord,
        *,
        response_metadata: _SafeResponseMetadata,
        failure: _ValidationFailure,
        cause: Exception,
    ) -> NoReturn:
        await self._mark_failed(
            record,
            "invalid_structured_output",
            response_metadata=response_metadata,
            validation_failure=failure,
        )
        raise ScriptingProviderContractError(
            "scripting provider output failed contract validation"
        ) from cause

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


def _safe_response_metadata(
    response: OpenAICompatibleResponse,
    transport: OpenAICompatibleResponsesClient,
) -> _SafeResponseMetadata:
    usage_value = response.body.get("usage")
    usage = usage_value if isinstance(usage_value, dict) else {}
    return _SafeResponseMetadata(
        http_status=response.http_status,
        provider_request_id=_safe_provider_request_id(response.request_id),
        reported_model=_safe_provider_model(transport.safe_string(response.body.get("model"))),
        input_tokens=transport.safe_int(
            usage.get("prompt_tokens", usage.get("input_tokens"))
        ),
        output_tokens=transport.safe_int(
            usage.get("completion_tokens", usage.get("output_tokens"))
        ),
        total_tokens=transport.safe_int(usage.get("total_tokens")),
        reported_cost_usd=_safe_decimal(usage.get("cost")),
        finish_reason=_safe_finish_reason(transport.extract_finish_reason(response.body)),
    )


def _safe_provider_request_id(value: str | None) -> str | None:
    return _safe_identifier(
        value,
        maximum=200,
        allowed_punctuation="_-.",
    )


def _safe_provider_model(value: str | None) -> str | None:
    return _safe_identifier(
        value,
        maximum=300,
        allowed_punctuation="_-./:@",
    )


def _safe_finish_reason(value: str | None) -> str | None:
    return _safe_identifier(
        value,
        maximum=100,
        allowed_punctuation="_-.",
    )


def _safe_identifier(
    value: str | None,
    *,
    maximum: int,
    allowed_punctuation: str,
) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped or not stripped.isascii() or len(stripped) > maximum:
        return None
    if any(not character.isalnum() and character not in allowed_punctuation for character in stripped):
        return None
    return stripped


def _protocol_error_message(code: OpenAICompatibleProtocolErrorCode) -> str:
    messages = {
        OpenAICompatibleProtocolErrorCode.RESPONSE_TOO_LARGE: (
            "provider response exceeded the safe size limit"
        ),
        OpenAICompatibleProtocolErrorCode.RESPONSE_JSON: (
            "provider envelope is not valid JSON"
        ),
        OpenAICompatibleProtocolErrorCode.RESPONSE_ENVELOPE: (
            "provider envelope is not a JSON object"
        ),
        OpenAICompatibleProtocolErrorCode.OUTPUT_TEXT: (
            "provider response has no single non-empty output text"
        ),
        OpenAICompatibleProtocolErrorCode.HTTP_STATUS: (
            "provider returned an unsupported HTTP status"
        ),
    }
    return messages[code]


def _pydantic_validation_failure(error: ValidationError) -> _ValidationFailure:
    details = error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )
    if not details:
        return _ValidationFailure(
            code=OpenRouterScriptingValidationErrorCode.UNKNOWN_STRUCTURED_OUTPUT_ERROR,
            path=None,
            message="script validation failed without details",
        )
    first = details[0]
    error_type = first.get("type")
    location = first.get("loc")
    path = _validation_path(location if isinstance(location, tuple) else ())
    if error_type == "extra_forbidden":
        code = OpenRouterScriptingValidationErrorCode.UNSUPPORTED_FIELD
        message = "extra fields are not permitted"
    elif not path and error_type == "value_error":
        code = OpenRouterScriptingValidationErrorCode.PRODUCTION_SCRIPT_CONTRACT
        message = _bounded_validation_message(first.get("msg"), "script contract is invalid")
    else:
        code = OpenRouterScriptingValidationErrorCode.PRODUCTION_SCRIPT_SCHEMA
        message = _bounded_validation_message(first.get("msg"), "script schema is invalid")
    return _ValidationFailure(code=code, path=path, message=message)


def _validation_path(location: tuple[object, ...]) -> str | None:
    result = ""
    for item in location:
        if isinstance(item, int) and item >= 0:
            result += f"[{item}]"
        elif isinstance(item, str) and item and all(
            character.isalnum() or character in "_-" for character in item
        ):
            result += ("." if result else "") + item
        else:
            return None
    return result[:300] or None


def _bounded_validation_message(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    normalized = " ".join(value.split())
    return normalized[:240] or fallback


def _plan_contract_failure(error: ValueError) -> _ValidationFailure:
    message = str(error)
    paths = {
        "source plan schema version does not match": "source_plan_schema_version",
        "script language does not match production plan": "language",
        "script target duration does not match production plan": "target_duration_seconds",
        "script must contain one ordered scene for every plan scene": "scenes",
        "script scene duration does not match source scene": "scenes",
    }
    scene_count_error = message == "script must contain one ordered scene for every plan scene"
    return _ValidationFailure(
        code=(
            OpenRouterScriptingValidationErrorCode.SCENE_COUNT_POLICY
            if scene_count_error
            else OpenRouterScriptingValidationErrorCode.PLAN_CONTRACT
        ),
        path=paths.get(message),
        message=message if message in paths else "script does not match production plan",
    )


def _duration_policy_message(error: ValueError) -> str:
    message = str(error)
    allowed = {
        "OpenRouter scripting supports local durations from 4 to 60 seconds",
        "scripting reading speed is outside supported bounds",
        "every script scene requires meaningful narration",
        "script narration is insufficient for the requested duration",
        "script narration exceeds the requested duration policy",
    }
    return message if message in allowed else "script violates the duration policy"


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
