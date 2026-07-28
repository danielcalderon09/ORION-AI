from datetime import timedelta
from decimal import Decimal

from backend.src.production.speech_generation.cost import (
    SpeechCostAuthorization,
    SpeechCostAuthorizationStatus,
    SpeechCostEstimator,
    SpeechPricingSnapshot,
    SpeechUsageMeasurement,
)
from backend.src.production.speech_generation.fingerprinting import (
    SpeechRemoteRequestFingerprintInput,
    speech_remote_request_fingerprint,
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
)
from backend.src.production.speech_generation.voice_selection import (
    SpeechVoiceFallbackPolicy,
    SpeechVoiceSelectionRequest,
    SpeechVoiceSelector,
)
from backend.tests.unit.production.speech_generation.conftest import (
    JOB_ID,
    NOW,
    SCRIPT_ID,
    SCRIPT_SHA,
)

SEGMENT_ID = "segment-" + "1" * 32
TEXT_HASH = "2" * 64


def capability_snapshot(
    *,
    provider: str = "fake-tts",
    modes: tuple[SpeechRemoteGenerationMode, ...] = (
        SpeechRemoteGenerationMode.SYNCHRONOUS,
        SpeechRemoteGenerationMode.ASYNCHRONOUS,
    ),
    pricing_unit: SpeechPricingUnit = SpeechPricingUnit.PER_CHARACTER,
) -> SpeechCapabilitySnapshot:
    pricing = (
        SpeechPricingCapability(
            currency="USD",
            pricing_unit=SpeechPricingUnit.UNKNOWN,
        )
        if pricing_unit is SpeechPricingUnit.UNKNOWN
        else SpeechPricingCapability(
            currency="USD",
            pricing_unit=pricing_unit,
            minimum_unit_price=Decimal("0.001"),
            maximum_unit_price=Decimal("0.002"),
            assumptions=("obviously fake deterministic test price",),
        )
    )
    return SpeechCapabilitySnapshot(
        capabilities=SpeechProviderCapabilities(
            provider=provider,
            models=(
                SpeechModelCapability(
                    model_id="fake-model",
                    voices=(
                        SpeechVoiceCapability(
                            voice_id="fake-voice",
                            languages=(
                                SpeechLanguageCapability(
                                    language="es-ES",
                                    supports_word_timing=True,
                                ),
                            ),
                            styles=("calm",),
                            supports_speaking_rate=True,
                            minimum_speaking_rate=Decimal("0.5"),
                            maximum_speaking_rate=Decimal("2"),
                        ),
                        SpeechVoiceCapability(
                            voice_id="fallback-voice",
                            languages=(SpeechLanguageCapability(language="es-ES"),),
                        ),
                    ),
                    default_voice_id="fallback-voice",
                    audio_formats=(
                        SpeechAudioFormatCapability(
                            audio_format=SpeechAudioFormat.WAV_PCM,
                            mime_type="audio/wav",
                            extension="wav",
                            sample_rates_hz=(24_000, 48_000),
                            channel_counts=(1, 2),
                            sample_width_bytes=(2,),
                        ),
                    ),
                    generation_modes=modes,
                    maximum_input_characters=1_000,
                    maximum_input_bytes=4_000,
                    maximum_output_duration_ms=120_000,
                    supports_timestamps=True,
                    supports_word_timing=True,
                    supports_provider_idempotency=True,
                    supports_cancellation=True,
                    pricing=pricing,
                ),
            ),
        ),
        audited_at=NOW,
        source=SpeechCapabilitySourceKind.STATIC_AUDITED,
        metadata={"fixture": True},
    )


def selection_request(**updates: object) -> SpeechVoiceSelectionRequest:
    values: dict[str, object] = {
        "provider": "fake-tts",
        "requested_model": "fake-model",
        "requested_voice": "fake-voice",
        "requested_language": "es-ES",
        "required_format": SpeechAudioFormat.WAV_PCM,
        "required_sample_rate_hz": 24_000,
        "required_channel_count": 1,
        "normalized_text_characters": 13,
        "normalized_text_bytes": 15,
        "requested_speaking_rate": Decimal("1"),
        "generation_mode": SpeechRemoteGenerationMode.ASYNCHRONOUS,
        "fallback_policy": SpeechVoiceFallbackPolicy.EXACT_ONLY,
    }
    values.update(updates)
    return SpeechVoiceSelectionRequest(**values)


def prepared_remote_record(**updates: object) -> RemoteSpeechJobRecord:
    snapshot = capability_snapshot()
    selection = SpeechVoiceSelector().select(
        snapshot=snapshot,
        request=selection_request(),
    )
    pricing = SpeechPricingSnapshot(
        provider=selection.provider,
        model=selection.model,
        voice=selection.voice,
        pricing=snapshot.capabilities.models[0].pricing,
        snapshot_at=NOW,
        source="fake-static",
    )
    estimate = SpeechCostEstimator().estimate(
        selection=selection,
        pricing=pricing,
        usage=SpeechUsageMeasurement(
            normalized_characters=13,
            utf8_bytes=15,
            estimated_tokens=4,
            estimated_duration_ms=1_000,
        ),
    )
    expectation = RemoteSpeechOutputExpectation(
        audio_format=SpeechAudioFormat.WAV_PCM,
        mime_type="audio/wav",
        extension="wav",
        sample_rate_hz=24_000,
        channel_count=1,
        maximum_duration_ms=120_000,
        maximum_audio_bytes=8_000_000,
    )
    fingerprint_input = SpeechRemoteRequestFingerprintInput(
        source_script_artifact_id=SCRIPT_ID,
        source_script_sha256=SCRIPT_SHA,
        segment_id=SEGMENT_ID,
        normalized_text_hash=TEXT_HASH,
        provider=selection.provider,
        model=selection.model,
        voice=selection.voice,
        language=selection.language,
        speaking_rate=Decimal("1"),
        audio_format=expectation.audio_format,
        sample_rate_hz=expectation.sample_rate_hz,
        channel_count=expectation.channel_count,
        capability_snapshot_hash=snapshot.snapshot_hash(),
        pricing_snapshot_hash=pricing.snapshot_hash(),
        generation_mode=selection.generation_mode,
    )
    values: dict[str, object] = {
        "job_id": JOB_ID,
        "attempt_number": 1,
        "segment_id": SEGMENT_ID,
        "provider": selection.provider,
        "model": selection.model,
        "voice": selection.voice,
        "language": selection.language,
        "speaking_rate": Decimal("1"),
        "generation_mode": selection.generation_mode,
        "source_script_artifact_id": SCRIPT_ID,
        "source_script_sha256": SCRIPT_SHA,
        "normalized_text_hash": TEXT_HASH,
        "request_fingerprint": speech_remote_request_fingerprint(fingerprint_input),
        "idempotency_key": "fake-idempotency-key-0001",
        "status": RemoteSpeechJobStatus.PREPARED,
        "prepared_at": NOW,
        "capability_snapshot_hash": snapshot.snapshot_hash(),
        "pricing_snapshot_hash": pricing.snapshot_hash(),
        "estimated_cost": estimate,
        "authorization": SpeechCostAuthorization(
            currency="USD",
            maximum_authorized_cost=Decimal("1.00"),
            status=SpeechCostAuthorizationStatus.AUTHORIZED,
            authorized_at=NOW,
            authorization_reference="fake-local-authorization",
        ),
        "output_expectation": expectation,
        "fresh_submission_permitted": True,
        "metadata": {"fixture": True},
    }
    values.update(updates)
    return RemoteSpeechJobRecord(**values)


def remote_record_for_status(
    status: RemoteSpeechJobStatus,
) -> RemoteSpeechJobRecord:
    updates: dict[str, object] = {"status": status}
    if status is not RemoteSpeechJobStatus.PREPARED:
        updates.update(
            {
                "submission_started_at": NOW + timedelta(seconds=1),
                "fresh_submission_permitted": False,
            }
        )
    if status in {
        RemoteSpeechJobStatus.SUBMITTED,
        RemoteSpeechJobStatus.PENDING,
        RemoteSpeechJobStatus.PROCESSING,
        RemoteSpeechJobStatus.COMPLETED,
        RemoteSpeechJobStatus.FAILED,
        RemoteSpeechJobStatus.CANCELLED,
        RemoteSpeechJobStatus.EXPIRED,
    }:
        updates.update(
            {
                "submitted_at": NOW + timedelta(seconds=2),
                "remote_job_id": "fake-remote-job-1",
            }
        )
    if status in {
        RemoteSpeechJobStatus.COMPLETED,
        RemoteSpeechJobStatus.FAILED,
        RemoteSpeechJobStatus.CANCELLED,
        RemoteSpeechJobStatus.EXPIRED,
    }:
        updates["terminal_at"] = NOW + timedelta(seconds=3)
    return prepared_remote_record(**updates)
