"""Strict provider-neutral speech capability snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.domain.base import ContractModel

SUPPORTED_SPEECH_CAPABILITY_VERSIONS = frozenset({"1.0.0"})


class SpeechRemoteGenerationMode(StrEnum):
    SYNCHRONOUS = "synchronous"
    ASYNCHRONOUS = "asynchronous"
    STREAMING = "streaming"


class SpeechAudioFormat(StrEnum):
    WAV_PCM = "wav_pcm"
    RAW_PCM = "raw_pcm"
    MP3 = "mp3"
    OGG = "ogg"


class SpeechCapabilitySourceKind(StrEnum):
    STATIC_SIMULATED = "static_simulated"
    DISABLED_REMOTE = "disabled_remote"
    STATIC_AUDITED = "static_audited"


class SpeechPricingUnit(StrEnum):
    PER_CHARACTER = "per_character"
    PER_BYTE = "per_byte"
    PER_TOKEN = "per_token"
    PER_SECOND = "per_second"
    PER_MINUTE = "per_minute"
    PER_REQUEST = "per_request"
    FIXED_PLUS_USAGE = "fixed_plus_usage"
    UNKNOWN = "unknown"


class SpeechPricingCapability(ContractModel):
    currency: str = Field(min_length=3, max_length=3)
    pricing_unit: SpeechPricingUnit
    usage_unit: SpeechPricingUnit | None = None
    minimum_unit_price: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=9)
    maximum_unit_price: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=9)
    fixed_base_cost: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=9)
    assumptions: tuple[str, ...] = Field(default=(), max_length=20)

    @field_validator(
        "minimum_unit_price",
        "maximum_unit_price",
        "fixed_base_cost",
        mode="before",
    )
    @classmethod
    def reject_float_money(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("speech pricing must not use float")
        return value

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value: str) -> str:
        normalized = value.upper()
        if not normalized.isalpha():
            raise ValueError("speech pricing currency must be an ISO-style code")
        return normalized

    @model_validator(mode="after")
    def validate_pricing(self) -> SpeechPricingCapability:
        if self.pricing_unit is SpeechPricingUnit.UNKNOWN:
            if (
                self.usage_unit is not None
                or self.minimum_unit_price is not None
                or self.maximum_unit_price is not None
                or self.fixed_base_cost != 0
            ):
                raise ValueError("unknown speech pricing cannot contain prices")
            return self
        usage_unit = (
            self.usage_unit
            if self.pricing_unit is SpeechPricingUnit.FIXED_PLUS_USAGE
            else self.pricing_unit
        )
        if usage_unit in {
            None,
            SpeechPricingUnit.FIXED_PLUS_USAGE,
            SpeechPricingUnit.UNKNOWN,
        }:
            raise ValueError("speech pricing usage unit is invalid")
        if self.pricing_unit is not SpeechPricingUnit.FIXED_PLUS_USAGE and self.usage_unit:
            raise ValueError("usage unit is only valid for fixed-plus-usage pricing")
        if self.minimum_unit_price is None or self.maximum_unit_price is None:
            raise ValueError("known speech pricing requires a bounded price range")
        if self.minimum_unit_price > self.maximum_unit_price:
            raise ValueError("speech minimum price exceeds maximum price")
        return self


class SpeechAudioFormatCapability(ContractModel):
    audio_format: SpeechAudioFormat
    mime_type: str = Field(min_length=1, max_length=100)
    extension: str = Field(pattern=r"^[a-z0-9]{2,8}$")
    sample_rates_hz: tuple[int, ...] = Field(min_length=1, max_length=20)
    channel_counts: tuple[int, ...] = Field(min_length=1, max_length=4)
    sample_width_bytes: tuple[int, ...] = Field(default=(), max_length=4)

    @model_validator(mode="after")
    def validate_format(self) -> SpeechAudioFormatCapability:
        for values, label in (
            (self.sample_rates_hz, "sample rates"),
            (self.channel_counts, "channel counts"),
            (self.sample_width_bytes, "sample widths"),
        ):
            if len(values) != len(set(values)) or any(value < 1 for value in values):
                raise ValueError(f"speech {label} must be positive and unique")
        if any(rate < 8_000 or rate > 192_000 for rate in self.sample_rates_hz):
            raise ValueError("speech sample rate capability is outside safe limits")
        return self


class SpeechLanguageCapability(ContractModel):
    language: str = Field(min_length=2, max_length=32)
    supports_word_timing: bool = False
    supports_character_timing: bool = False

    @field_validator("language")
    @classmethod
    def canonical_language(cls, value: str) -> str:
        parts = value.split("-")
        if not all(part.isalnum() for part in parts):
            raise ValueError("speech language capability is invalid")
        return "-".join((parts[0].lower(), *(part.upper() for part in parts[1:])))


class SpeechVoiceCapability(ContractModel):
    voice_id: str = Field(min_length=1, max_length=200)
    languages: tuple[SpeechLanguageCapability, ...] = Field(min_length=1, max_length=100)
    styles: tuple[str, ...] = Field(default=(), max_length=100)
    supports_speaking_rate: bool = False
    minimum_speaking_rate: Decimal | None = Field(default=None, gt=0, le=4)
    maximum_speaking_rate: Decimal | None = Field(default=None, gt=0, le=4)
    supports_pitch: bool = False
    supports_emotion: bool = False

    @field_validator(
        "minimum_speaking_rate",
        "maximum_speaking_rate",
        mode="before",
    )
    @classmethod
    def reject_float_rate(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("speech speaking-rate capability must not use float")
        return value

    @model_validator(mode="after")
    def validate_voice(self) -> SpeechVoiceCapability:
        language_ids = tuple(item.language.casefold() for item in self.languages)
        if len(language_ids) != len(set(language_ids)):
            raise ValueError("speech voice languages must be unique")
        if len(self.styles) != len(set(self.styles)):
            raise ValueError("speech voice styles must be unique")
        minimum_rate = self.minimum_speaking_rate
        maximum_rate = self.maximum_speaking_rate
        has_bounds = minimum_rate is not None and maximum_rate is not None
        if self.supports_speaking_rate != has_bounds:
            raise ValueError("speech speaking-rate support and bounds differ")
        if minimum_rate is not None and maximum_rate is not None and minimum_rate > maximum_rate:
            raise ValueError("speech speaking-rate bounds are reversed")
        return self


class SpeechModelCapability(ContractModel):
    model_id: str = Field(min_length=1, max_length=300)
    voices: tuple[SpeechVoiceCapability, ...] = Field(min_length=1, max_length=500)
    default_voice_id: str | None = Field(default=None, min_length=1, max_length=200)
    audio_formats: tuple[SpeechAudioFormatCapability, ...] = Field(min_length=1, max_length=20)
    generation_modes: tuple[SpeechRemoteGenerationMode, ...] = Field(min_length=1, max_length=3)
    maximum_input_characters: int = Field(gt=0, le=5_000_000)
    maximum_input_bytes: int = Field(gt=0, le=20_000_000)
    maximum_output_duration_ms: int = Field(gt=0, le=7_200_000)
    deterministic_output_claim: bool = False
    supports_timestamps: bool = False
    supports_word_timing: bool = False
    supports_character_timing: bool = False
    supports_provider_idempotency: bool = False
    supports_cancellation: bool = False
    pricing: SpeechPricingCapability

    @model_validator(mode="after")
    def validate_model(self) -> SpeechModelCapability:
        voices = tuple(item.voice_id for item in self.voices)
        formats = tuple(item.audio_format for item in self.audio_formats)
        if len(voices) != len(set(voices)):
            raise ValueError("speech model voices must be unique")
        if len(formats) != len(set(formats)):
            raise ValueError("speech model formats must be unique")
        if len(self.generation_modes) != len(set(self.generation_modes)):
            raise ValueError("speech generation modes must be unique")
        if self.default_voice_id is not None and self.default_voice_id not in voices:
            raise ValueError("speech model default voice is not declared")
        if self.supports_character_timing and not self.supports_word_timing:
            raise ValueError("character timing requires word timing support")
        if self.supports_word_timing and not self.supports_timestamps:
            raise ValueError("word timing requires timestamp support")
        return self


class SpeechProviderCapabilities(ContractModel):
    provider: str = Field(min_length=1, max_length=100)
    models: tuple[SpeechModelCapability, ...] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_models(self) -> SpeechProviderCapabilities:
        model_ids = tuple(item.model_id for item in self.models)
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("speech provider models must be unique")
        return self


class SpeechCapabilitySnapshot(ContractModel):
    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    capabilities: SpeechProviderCapabilities
    audited_at: datetime
    source: SpeechCapabilitySourceKind
    metadata: dict[str, bool | int | str] = Field(default_factory=dict)

    @field_validator("audited_at")
    @classmethod
    def aware_audit_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("speech capability audit time must be timezone-aware")
        return value

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, bool | int | str]) -> dict[str, bool | int | str]:
        checked = validate_safe_json(value, path="speech_capability_snapshot.metadata")
        if not isinstance(checked, dict):
            raise ValueError("speech capability metadata must be an object")
        return checked

    @model_validator(mode="after")
    def supported_version(self) -> SpeechCapabilitySnapshot:
        if self.schema_version not in SUPPORTED_SPEECH_CAPABILITY_VERSIONS:
            raise ValueError("unsupported speech capability schema version")
        return self

    def snapshot_hash(self) -> str:
        payload = self.model_dump(mode="json")
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()
