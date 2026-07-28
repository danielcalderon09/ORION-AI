"""Offline capability sources for simulated and disabled speech providers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from backend.src.production.speech_generation.configuration import (
    SpeechGenerationConfiguration,
)
from backend.src.production.speech_generation.exceptions import (
    SpeechCapabilityConfigurationError,
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


class StaticSimulatedSpeechCapabilitySource:
    """Describe the existing tone provider without discovery or I/O."""

    def __init__(
        self,
        *,
        configuration: SpeechGenerationConfiguration,
        clock: Callable[[], datetime],
    ) -> None:
        self._configuration = configuration
        self._clock = clock
        self._closed = False

    async def discover_capabilities(self) -> SpeechCapabilitySnapshot:
        if self._closed:
            raise SpeechCapabilityConfigurationError("simulated speech capability source is closed")
        configuration = self._configuration
        return SpeechCapabilitySnapshot(
            capabilities=SpeechProviderCapabilities(
                provider="orion-simulated-speech",
                models=(
                    SpeechModelCapability(
                        model_id="simulated-tone-v1",
                        voices=(
                            SpeechVoiceCapability(
                                voice_id=configuration.voice,
                                languages=(
                                    SpeechLanguageCapability(
                                        language=configuration.language,
                                    ),
                                ),
                            ),
                        ),
                        default_voice_id=configuration.voice,
                        audio_formats=(
                            SpeechAudioFormatCapability(
                                audio_format=SpeechAudioFormat.WAV_PCM,
                                mime_type="audio/wav",
                                extension="wav",
                                sample_rates_hz=(configuration.sample_rate_hz,),
                                channel_counts=(configuration.channel_count,),
                                sample_width_bytes=(configuration.sample_width_bytes,),
                            ),
                        ),
                        generation_modes=(SpeechRemoteGenerationMode.SYNCHRONOUS,),
                        maximum_input_characters=100_000,
                        maximum_input_bytes=400_000,
                        maximum_output_duration_ms=(configuration.max_segment_duration_ms),
                        deterministic_output_claim=True,
                        pricing=SpeechPricingCapability(
                            currency="USD",
                            pricing_unit=SpeechPricingUnit.PER_REQUEST,
                            minimum_unit_price=Decimal("0"),
                            maximum_unit_price=Decimal("0"),
                            assumptions=("offline simulated output",),
                        ),
                    ),
                ),
            ),
            audited_at=self._clock(),
            source=SpeechCapabilitySourceKind.STATIC_SIMULATED,
            metadata={"network": False, "simulated": True},
        )

    async def close(self) -> None:
        self._closed = True


class DisabledRemoteSpeechCapabilitySource:
    """Fail before discovery because no real provider exists in Phase 5G.2."""

    async def discover_capabilities(self) -> SpeechCapabilitySnapshot:
        raise SpeechCapabilityConfigurationError("remote speech capability discovery is disabled")

    async def close(self) -> None:
        return None
