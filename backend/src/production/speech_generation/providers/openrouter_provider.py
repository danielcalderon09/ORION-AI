"""Durable single-attempt OpenRouter speech adapter."""

from __future__ import annotations

import asyncio
import hashlib
import io
import re
import wave
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from backend.src.production.speech_generation.billable_gate import (
    SpeechBillableRequestGate,
    SpeechBillableRequestPolicy,
)
from backend.src.production.speech_generation.cost import (
    SpeechCostAuthorization,
    SpeechCostAuthorizationStatus,
    SpeechCostConfidence,
    SpeechCostEstimate,
    SpeechPricingSnapshot,
)
from backend.src.production.speech_generation.exceptions import (
    SpeechBillableAuthorizationError,
    SpeechProviderClosedError,
    SpeechProviderResponseError,
    SpeechProviderUncertainError,
)
from backend.src.production.speech_generation.fingerprinting import (
    SpeechRemoteRequestFingerprintInput,
    speech_remote_request_fingerprint,
)
from backend.src.production.speech_generation.models import SpeechSegmentAudioMetadata
from backend.src.production.speech_generation.ports import (
    SpeechProviderRequest,
    SpeechProviderResult,
)
from backend.src.production.speech_generation.remote_capabilities import (
    SpeechAudioFormat,
    SpeechAudioFormatCapability,
    SpeechCapabilitySnapshot,
    SpeechCapabilitySourceKind,
    SpeechLanguageCapability,
    SpeechModelCapability,
    SpeechPricingCapability,
    SpeechPricingUnit,
    SpeechProviderCapabilities,
    SpeechRemoteGenerationMode,
    SpeechVoiceCapability,
)
from backend.src.production.speech_generation.remote_models import (
    RemoteSpeechJobRecord,
    RemoteSpeechJobStatus,
    RemoteSpeechOutputExpectation,
    RemoteSpeechOutputMetadata,
)
from backend.src.production.speech_generation.remote_ports import RemoteSpeechJobStore

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,300}$")
_SAFE_PCM_MIME = frozenset(
    {"audio/pcm", "application/octet-stream", "binary/octet-stream"}
)


class OpenRouterSpeechGenerationProvider:
    """POST one Kokoro request to `/audio/speech` with no automatic retry."""

    name = "openrouter"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        voice: str,
        estimated_cost_usd: Decimal,
        maximum_authorized_cost_usd: Decimal,
        allow_billable_requests: bool,
        remote_job_store: RemoteSpeechJobStore,
        maximum_requests_per_job: int = 1,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout_seconds: float = 120,
        max_audio_bytes: int = 8_000_000,
        client: httpx.AsyncClient | None = None,
        owns_client: bool = False,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not api_key.strip() or not model.strip() or not voice.strip():
            raise ValueError("OpenRouter speech credential, model, and voice are required")
        if not allow_billable_requests:
            raise SpeechBillableAuthorizationError("billable speech requests are disabled")
        if estimated_cost_usd <= 0 or maximum_authorized_cost_usd < estimated_cost_usd:
            raise SpeechBillableAuthorizationError("speech cost authorization is invalid")
        if not 1 <= maximum_requests_per_job <= 50:
            raise ValueError("speech request limit is outside safe bounds")
        if estimated_cost_usd * maximum_requests_per_job > maximum_authorized_cost_usd:
            raise SpeechBillableAuthorizationError(
                "speech job cost authorization is invalid"
            )
        parsed = httpx.URL(base_url)
        if (
            parsed.scheme != "https"
            or parsed.host != "openrouter.ai"
            or parsed.userinfo
            or parsed.path.rstrip("/") != "/api/v1"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("OpenRouter speech base URL is invalid")
        if timeout_seconds <= 0 or not 1_024 <= max_audio_bytes <= 50_000_000:
            raise ValueError("OpenRouter speech limits are invalid")
        self._model = model.strip()
        self._voice = voice.strip()
        self._estimated_cost = estimated_cost_usd
        self._maximum_cost = maximum_authorized_cost_usd
        self._store = remote_job_store
        self._maximum_requests = maximum_requests_per_job
        self._base_url = str(parsed).rstrip("/")
        self._timeout = httpx.Timeout(timeout_seconds)
        self._max_audio_bytes = max_audio_bytes
        self._client = client
        self._owns_client = client is None or owns_client
        self._clock = clock
        self._closed = False
        self._headers = {
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
            "Accept": "application/octet-stream",
        }

    async def generate(self, request: SpeechProviderRequest) -> SpeechProviderResult:
        if self._closed:
            raise SpeechProviderClosedError("speech provider is closed")
        existing = await self._store.read(
            job_id=request.job_id,
            attempt_number=request.attempt_number,
            segment_id=request.segment.segment_id,
        )
        if existing is not None:
            raise SpeechProviderUncertainError(
                "durable remote speech request already exists without reusable local audio"
            )
        records = await self._store.list_records()
        job_records = tuple(record for record in records if record.job_id == request.job_id)
        if len(job_records) >= self._maximum_requests:
            raise SpeechBillableAuthorizationError("speech job request limit was reached")
        if any(record.status is RemoteSpeechJobStatus.UNCERTAIN for record in job_records):
            raise SpeechProviderUncertainError("uncertain speech submission requires review")

        prepared = self._prepared_record(request)
        await self._store.create(prepared)
        snapshot = self._capability_snapshot(request, prepared.prepared_at)
        SpeechBillableRequestGate().authorize(
            policy=SpeechBillableRequestPolicy(
                allow_billable_requests=True,
                remote_provider="openrouter",
                provider_configuration_valid=True,
                live_adapter_available=True,
            ),
            record=prepared,
            capability_snapshot=snapshot,
            unresolved_uncertain_submission=False,
            authorized_at=prepared.prepared_at,
        )
        started = self._now()
        submitting = prepared.model_copy(
            update={
                "status": RemoteSpeechJobStatus.SUBMITTING,
                "submission_started_at": started,
                "fresh_submission_permitted": False,
            }
        )
        await self._store.checkpoint(previous=prepared, current=submitting)
        response_status: int | None = None
        response_generation_id: str | None = None
        try:
            content, status, generation_id, content_type = await self._post_once(request)
            response_status = status
            response_generation_id = generation_id
            if status not in range(200, 300):
                failed = self._failed(submitting, status=status, generation_id=generation_id)
                await self._store.checkpoint(previous=submitting, current=failed)
                raise SpeechProviderResponseError(
                    "OpenRouter speech request was rejected",
                    http_status=status,
                    provider_request_id=generation_id,
                )
            normalized_mime = content_type.split(";", 1)[0].strip() if content_type else None
            if normalized_mime is not None and normalized_mime not in _SAFE_PCM_MIME:
                raise SpeechProviderResponseError(
                    "OpenRouter speech media type is unsupported",
                    http_status=status,
                    provider_request_id=generation_id,
                )
            wav_content, audio = self._wrap_pcm(content, request)
            finished = self._now()
            completed = submitting.model_copy(
                update={
                    "status": RemoteSpeechJobStatus.COMPLETED,
                    "submitted_at": finished,
                    "terminal_at": finished,
                    "remote_generation_id": generation_id,
                    "output": RemoteSpeechOutputMetadata(
                        sha256=hashlib.sha256(wav_content).hexdigest(),
                        size_bytes=len(wav_content),
                        duration_ms=audio.duration_ms,
                        sample_rate_hz=audio.sample_rate_hz,
                        channel_count=audio.channel_count,
                        mime_type="audio/wav",
                        downloaded_at=finished,
                    ),
                    "metadata": {
                        "endpoint": "api_v1_audio_speech",
                        "http_status": status,
                        "response_format": "pcm",
                    },
                }
            )
            await self._store.checkpoint(previous=submitting, current=completed)
            metadata: dict[str, bool | int | str] = {
                "network": True,
                "simulated": False,
                "http_status": status,
                "requested_model": self._model,
                "request_fingerprint": completed.request_fingerprint,
                "response_format": "pcm",
            }
            if generation_id is not None:
                metadata["provider_generation_id"] = generation_id
            return SpeechProviderResult(
                content=wav_content,
                provider=self.name,
                audio=audio,
                deterministic=False,
                metadata=metadata,
            )
        except asyncio.CancelledError:
            await self._mark_uncertain(submitting)
            raise
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            await self._mark_uncertain(submitting)
            raise SpeechProviderUncertainError(
                "OpenRouter speech submission outcome is uncertain"
            ) from exc
        except SpeechProviderResponseError as exc:
            current = await self._store.read(
                job_id=request.job_id,
                attempt_number=request.attempt_number,
                segment_id=request.segment.segment_id,
            )
            if current == submitting:
                failed = self._failed(
                    submitting,
                    status=exc.http_status or response_status or 200,
                    generation_id=(
                        exc.provider_request_id or response_generation_id
                    ),
                )
                await self._store.checkpoint(previous=submitting, current=failed)
            raise

    async def _post_once(
        self, request: SpeechProviderRequest
    ) -> tuple[bytes, int, str | None, str | None]:
        async with self._get_client().stream(
            "POST",
            f"{self._base_url}/audio/speech",
            json={
                "model": self._model,
                "input": request.segment.narration_text,
                "voice": self._voice,
                "response_format": "pcm",
            },
            headers=self._headers,
            timeout=self._timeout,
            follow_redirects=False,
        ) as response:
            content = await _read_bounded(response, self._max_audio_bytes)
            return (
                content,
                response.status_code,
                _safe_generation_id(response.headers.get("x-generation-id")),
                response.headers.get("content-type"),
            )

    def _prepared_record(self, request: SpeechProviderRequest) -> RemoteSpeechJobRecord:
        now = self._now()
        snapshot = self._capability_snapshot(request, now)
        pricing = self._pricing_snapshot(now)
        output = RemoteSpeechOutputExpectation(
            audio_format=SpeechAudioFormat.WAV_PCM,
            mime_type="audio/wav",
            extension="wav",
            sample_rate_hz=request.configuration.sample_rate_hz,
            channel_count=request.configuration.channel_count,
            maximum_duration_ms=request.configuration.max_segment_duration_ms,
            maximum_audio_bytes=request.configuration.max_audio_bytes,
        )
        options: dict[str, bool | int | str] = {
            "provider_request_schema_version": "1.0.0",
            "response_format": "pcm",
        }
        fingerprint = speech_remote_request_fingerprint(
            SpeechRemoteRequestFingerprintInput(
                source_script_artifact_id=request.segment.source_script_artifact_id,
                source_script_sha256=request.segment.source_script_sha256,
                segment_id=request.segment.segment_id,
                normalized_text_hash=request.segment.normalized_text_hash,
                provider="openrouter",
                model=self._model,
                voice=self._voice,
                language=request.segment.requested_language,
                speaking_rate=None,
                audio_format=output.audio_format,
                sample_rate_hz=output.sample_rate_hz,
                channel_count=output.channel_count,
                capability_snapshot_hash=snapshot.snapshot_hash(),
                pricing_snapshot_hash=pricing.snapshot_hash(),
                generation_mode=SpeechRemoteGenerationMode.SYNCHRONOUS,
                options=options,
            )
        )
        estimate = SpeechCostEstimate(
            provider="openrouter",
            model=self._model,
            voice=self._voice,
            currency="USD",
            pricing_unit=SpeechPricingUnit.PER_REQUEST,
            estimated_billable_quantity=Decimal(1),
            estimated_minimum_cost=self._estimated_cost,
            estimated_maximum_cost=self._estimated_cost,
            pricing_snapshot_at=now,
            pricing_snapshot_hash=pricing.snapshot_hash(),
            calculation_method="explicit_user_configured_per_request_estimate",
            assumptions=("runtime price is not inferred",),
            confidence=SpeechCostConfidence.EXACT,
        )
        return RemoteSpeechJobRecord(
            job_id=request.job_id,
            attempt_number=request.attempt_number,
            segment_id=request.segment.segment_id,
            provider="openrouter",
            model=self._model,
            voice=self._voice,
            language=request.segment.requested_language,
            generation_mode=SpeechRemoteGenerationMode.SYNCHRONOUS,
            source_script_artifact_id=request.segment.source_script_artifact_id,
            source_script_sha256=request.segment.source_script_sha256,
            normalized_text_hash=request.segment.normalized_text_hash,
            request_fingerprint=fingerprint,
            status=RemoteSpeechJobStatus.PREPARED,
            prepared_at=now,
            capability_snapshot_hash=snapshot.snapshot_hash(),
            pricing_snapshot_hash=pricing.snapshot_hash(),
            estimated_cost=estimate,
            authorization=SpeechCostAuthorization(
                currency="USD",
                maximum_authorized_cost=self._maximum_cost,
                status=SpeechCostAuthorizationStatus.AUTHORIZED,
                authorized_at=now,
                authorization_reference="explicit_local_configuration",
            ),
            output_expectation=output,
            fresh_submission_permitted=True,
            options=options,
            metadata={"endpoint": "api_v1_audio_speech"},
        )

    def _capability_snapshot(
        self, request: SpeechProviderRequest, audited_at: datetime
    ) -> SpeechCapabilitySnapshot:
        pricing = SpeechPricingCapability(
            currency="USD",
            pricing_unit=SpeechPricingUnit.PER_REQUEST,
            minimum_unit_price=self._estimated_cost,
            maximum_unit_price=self._estimated_cost,
            assumptions=("explicit local estimate",),
        )
        return SpeechCapabilitySnapshot(
            capabilities=SpeechProviderCapabilities(
                provider="openrouter",
                models=(
                    SpeechModelCapability(
                        model_id=self._model,
                        voices=(
                            SpeechVoiceCapability(
                                voice_id=self._voice,
                                languages=(
                                    SpeechLanguageCapability(
                                        language=request.segment.requested_language
                                    ),
                                ),
                            ),
                        ),
                        audio_formats=(
                            SpeechAudioFormatCapability(
                                audio_format=SpeechAudioFormat.WAV_PCM,
                                mime_type="audio/wav",
                                extension="wav",
                                sample_rates_hz=(request.configuration.sample_rate_hz,),
                                channel_counts=(request.configuration.channel_count,),
                                sample_width_bytes=(request.configuration.sample_width_bytes,),
                            ),
                        ),
                        generation_modes=(SpeechRemoteGenerationMode.SYNCHRONOUS,),
                        maximum_input_characters=6_000,
                        maximum_input_bytes=24_000,
                        maximum_output_duration_ms=request.configuration.max_segment_duration_ms,
                        pricing=pricing,
                    ),
                ),
            ),
            audited_at=audited_at,
            source=SpeechCapabilitySourceKind.STATIC_AUDITED,
            metadata={"endpoint": "api_v1_audio_speech", "configured_voice": True},
        )

    def _pricing_snapshot(self, now: datetime) -> SpeechPricingSnapshot:
        return SpeechPricingSnapshot(
            provider="openrouter",
            model=self._model,
            voice=self._voice,
            pricing=SpeechPricingCapability(
                currency="USD",
                pricing_unit=SpeechPricingUnit.PER_REQUEST,
                minimum_unit_price=self._estimated_cost,
                maximum_unit_price=self._estimated_cost,
                assumptions=("explicit local estimate",),
            ),
            snapshot_at=now,
            source="explicit_local_configuration",
        )

    def _wrap_pcm(
        self, content: bytes, request: SpeechProviderRequest
    ) -> tuple[bytes, SpeechSegmentAudioMetadata]:
        if not content or len(content) > self._max_audio_bytes or len(content) % 2:
            raise SpeechProviderResponseError("OpenRouter speech PCM payload is invalid")
        configuration = request.configuration
        frame_count = len(content) // (
            configuration.channel_count * configuration.sample_width_bytes
        )
        duration_ms = round(frame_count * 1_000 / configuration.sample_rate_hz)
        if (
            frame_count < 1
            or duration_ms < configuration.min_duration_ms
            or duration_ms > configuration.max_segment_duration_ms
        ):
            raise SpeechProviderResponseError("OpenRouter speech duration is outside safe limits")
        output = io.BytesIO()
        with wave.open(output, "wb") as writer:
            writer.setnchannels(configuration.channel_count)
            writer.setsampwidth(configuration.sample_width_bytes)
            writer.setframerate(configuration.sample_rate_hz)
            writer.writeframes(content)
        wav_content = output.getvalue()
        if len(wav_content) > configuration.max_audio_bytes:
            raise SpeechProviderResponseError("OpenRouter speech WAV exceeds the configured limit")
        return wav_content, SpeechSegmentAudioMetadata(
            duration_ms=duration_ms,
            sample_rate_hz=configuration.sample_rate_hz,
            channel_count=configuration.channel_count,
            sample_width_bytes=configuration.sample_width_bytes,
            frame_count=frame_count,
        )

    def _failed(
        self,
        record: RemoteSpeechJobRecord,
        *,
        status: int,
        generation_id: str | None,
    ) -> RemoteSpeechJobRecord:
        now = self._now()
        return record.model_copy(
            update={
                "status": RemoteSpeechJobStatus.FAILED,
                "submitted_at": now,
                "terminal_at": now,
                "remote_generation_id": generation_id,
                "safe_error_code": "provider_http_error" if status != 200 else "invalid_audio",
                "metadata": {"endpoint": "api_v1_audio_speech", "http_status": status},
            }
        )

    async def _mark_uncertain(self, record: RemoteSpeechJobRecord) -> None:
        current = await self._store.read(
            job_id=record.job_id,
            attempt_number=record.attempt_number,
            segment_id=record.segment_id,
        )
        if current == record:
            uncertain = record.model_copy(
                update={
                    "status": RemoteSpeechJobStatus.UNCERTAIN,
                    "safe_error_code": "submission_outcome_uncertain",
                }
            )
            await self._store.checkpoint(previous=record, current=uncertain)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(follow_redirects=False, trust_env=False)
        return self._client

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("speech provider clock must be timezone-aware")
        return value


async def _read_bounded(response: httpx.Response, maximum: int) -> bytes:
    result = bytearray()
    async for chunk in response.aiter_bytes():
        result.extend(chunk)
        if len(result) > maximum:
            raise SpeechProviderResponseError(
                "OpenRouter speech response exceeds safe limits",
                http_status=response.status_code,
                provider_request_id=_safe_generation_id(
                    response.headers.get("x-generation-id")
                ),
            )
    return bytes(result)


def _safe_generation_id(value: Any) -> str | None:
    return value if isinstance(value, str) and _SAFE_ID.fullmatch(value) else None


__all__ = ["OpenRouterSpeechGenerationProvider"]
