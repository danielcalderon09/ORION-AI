"""Deterministic provider-neutral speech model and voice selection."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.speech_generation.exceptions import (
    SpeechVoiceSelectionError,
)
from backend.src.production.speech_generation.remote_capabilities import (
    SpeechAudioFormat,
    SpeechCapabilitySnapshot,
    SpeechPricingUnit,
    SpeechRemoteGenerationMode,
)


class SpeechVoiceFallbackPolicy(StrEnum):
    EXACT_ONLY = "exact_only"
    EXPLICIT_MODEL_DEFAULT = "explicit_model_default"


class SpeechVoiceSelectionRequest(ContractModel):
    provider: str = Field(min_length=1, max_length=100)
    requested_model: str = Field(min_length=1, max_length=300)
    requested_voice: str | None = Field(default=None, min_length=1, max_length=200)
    requested_language: str = Field(min_length=2, max_length=32)
    required_format: SpeechAudioFormat
    required_sample_rate_hz: int = Field(ge=8_000, le=192_000)
    required_channel_count: int = Field(ge=1, le=8)
    normalized_text_characters: int = Field(gt=0, le=5_000_000)
    normalized_text_bytes: int = Field(gt=0, le=20_000_000)
    requested_speaking_rate: Decimal | None = Field(default=None, gt=0, le=4)
    required_style: str | None = Field(default=None, min_length=1, max_length=100)
    require_timestamps: bool = False
    require_word_timing: bool = False
    require_character_timing: bool = False
    generation_mode: SpeechRemoteGenerationMode
    budget_ceiling: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=9)
    fallback_policy: SpeechVoiceFallbackPolicy = SpeechVoiceFallbackPolicy.EXACT_ONLY

    @field_validator(
        "requested_speaking_rate",
        "budget_ceiling",
        mode="before",
    )
    @classmethod
    def reject_float_decimal(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("speech selection decimals must not use float")
        return value

    @field_validator("requested_language")
    @classmethod
    def canonical_language(cls, value: str) -> str:
        parts = value.split("-")
        if not all(part.isalnum() for part in parts):
            raise ValueError("requested speech language is invalid")
        return "-".join((parts[0].lower(), *(part.upper() for part in parts[1:])))


class SpeechVoiceSelection(ContractModel):
    provider: str
    model: str
    voice: str
    language: str
    audio_format: SpeechAudioFormat
    sample_rate_hz: int
    channel_count: int
    generation_mode: SpeechRemoteGenerationMode
    capability_snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    fallback_policy: SpeechVoiceFallbackPolicy
    selection_reason: str = Field(min_length=1, max_length=200)


class SpeechVoiceSelector:
    def select(
        self,
        *,
        snapshot: SpeechCapabilitySnapshot,
        request: SpeechVoiceSelectionRequest,
    ) -> SpeechVoiceSelection:
        capabilities = snapshot.capabilities
        if capabilities.provider != request.provider:
            raise SpeechVoiceSelectionError("speech capability provider does not match request")
        model = next(
            (item for item in capabilities.models if item.model_id == request.requested_model),
            None,
        )
        if model is None:
            raise SpeechVoiceSelectionError("requested speech model is unavailable")
        if request.generation_mode not in model.generation_modes:
            raise SpeechVoiceSelectionError("requested speech generation mode is unavailable")
        if request.normalized_text_characters > model.maximum_input_characters:
            raise SpeechVoiceSelectionError("speech text exceeds model character limit")
        if request.normalized_text_bytes > model.maximum_input_bytes:
            raise SpeechVoiceSelectionError("speech text exceeds model byte limit")
        format_capability = next(
            (item for item in model.audio_formats if item.audio_format is request.required_format),
            None,
        )
        if format_capability is None:
            raise SpeechVoiceSelectionError("required speech format is unavailable")
        if request.required_sample_rate_hz not in format_capability.sample_rates_hz:
            raise SpeechVoiceSelectionError("required speech sample rate is unavailable")
        if request.required_channel_count not in format_capability.channel_counts:
            raise SpeechVoiceSelectionError("required speech channel count is unavailable")
        voice_id = request.requested_voice
        reason = "exact voice"
        if voice_id is None:
            if (
                request.fallback_policy is not SpeechVoiceFallbackPolicy.EXPLICIT_MODEL_DEFAULT
                or model.default_voice_id is None
            ):
                raise SpeechVoiceSelectionError("speech voice fallback was not authorized")
            voice_id = model.default_voice_id
            reason = "explicit model default"
        voice = next((item for item in model.voices if item.voice_id == voice_id), None)
        if voice is None:
            raise SpeechVoiceSelectionError("requested speech voice is unavailable")
        language = next(
            (
                item
                for item in voice.languages
                if item.language.casefold() == request.requested_language.casefold()
            ),
            None,
        )
        if language is None:
            raise SpeechVoiceSelectionError("requested speech language is unavailable")
        if request.requested_speaking_rate is not None and (
            not voice.supports_speaking_rate
            or voice.minimum_speaking_rate is None
            or voice.maximum_speaking_rate is None
            or not (
                voice.minimum_speaking_rate
                <= request.requested_speaking_rate
                <= voice.maximum_speaking_rate
            )
        ):
            raise SpeechVoiceSelectionError("requested speaking rate is unavailable")
        if request.required_style is not None and request.required_style not in voice.styles:
            raise SpeechVoiceSelectionError("requested speech style is unavailable")
        if request.require_timestamps and not model.supports_timestamps:
            raise SpeechVoiceSelectionError("speech timestamps are unavailable")
        if request.require_word_timing and (
            not model.supports_word_timing or not language.supports_word_timing
        ):
            raise SpeechVoiceSelectionError("word-level speech timing is unavailable")
        if request.require_character_timing and (
            not model.supports_character_timing or not language.supports_character_timing
        ):
            raise SpeechVoiceSelectionError("character-level speech timing is unavailable")
        if (
            request.budget_ceiling is not None
            and model.pricing.pricing_unit is SpeechPricingUnit.UNKNOWN
        ):
            raise SpeechVoiceSelectionError("speech pricing is unknown")
        return SpeechVoiceSelection(
            provider=request.provider,
            model=model.model_id,
            voice=voice.voice_id,
            language=language.language,
            audio_format=format_capability.audio_format,
            sample_rate_hz=request.required_sample_rate_hz,
            channel_count=request.required_channel_count,
            generation_mode=request.generation_mode,
            capability_snapshot_hash=snapshot.snapshot_hash(),
            fallback_policy=request.fallback_policy,
            selection_reason=reason,
        )
