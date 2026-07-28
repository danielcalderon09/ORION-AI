from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.src.production.speech_generation.exceptions import (
    SpeechVoiceSelectionError,
)
from backend.src.production.speech_generation.remote_capabilities import (
    SpeechAudioFormat,
    SpeechCapabilitySnapshot,
    SpeechRemoteGenerationMode,
)
from backend.src.production.speech_generation.voice_selection import (
    SpeechVoiceFallbackPolicy,
    SpeechVoiceSelector,
)
from backend.tests.unit.production.speech_generation.remote_test_helpers import (
    capability_snapshot,
    selection_request,
)


def test_capability_snapshot_is_strict_immutable_and_deterministically_hashed() -> None:
    first = capability_snapshot()
    second = capability_snapshot()

    assert first.snapshot_hash() == second.snapshot_hash()
    with pytest.raises(ValidationError):
        first.source = "changed"
    with pytest.raises(ValidationError):
        SpeechCapabilitySnapshot.model_validate(
            {**first.model_dump(mode="python"), "unexpected": True}
        )
    with pytest.raises(ValidationError, match="unsupported"):
        SpeechCapabilitySnapshot.model_validate(
            {**first.model_dump(mode="python"), "schema_version": "2.0.0"}
        )


def test_capability_models_and_voices_must_be_unique() -> None:
    snapshot = capability_snapshot()
    provider = snapshot.capabilities
    with pytest.raises(ValidationError, match="models must be unique"):
        type(provider)(
            provider=provider.provider,
            models=(provider.models[0], provider.models[0]),
        )
    model = provider.models[0]
    with pytest.raises(ValidationError, match="voices must be unique"):
        type(model).model_validate(
            {
                **model.model_dump(mode="python"),
                "voices": (model.voices[0], model.voices[0]),
            }
        )


def test_capability_metadata_rejects_sensitive_fields() -> None:
    snapshot = capability_snapshot()
    with pytest.raises(ValidationError, match="sensitive"):
        SpeechCapabilitySnapshot.model_validate(
            {
                **snapshot.model_dump(mode="python"),
                "metadata": {"authorization": "Bearer fake"},
            }
        )


def test_exact_voice_language_and_format_selection_is_deterministic() -> None:
    snapshot = capability_snapshot()
    request = selection_request()

    first = SpeechVoiceSelector().select(snapshot=snapshot, request=request)
    second = SpeechVoiceSelector().select(snapshot=snapshot, request=request)

    assert first == second
    assert first.voice == "fake-voice"
    assert first.language == "es-ES"
    assert first.audio_format is SpeechAudioFormat.WAV_PCM
    assert first.generation_mode is SpeechRemoteGenerationMode.ASYNCHRONOUS


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"requested_voice": "missing"}, "voice"),
        ({"requested_language": "en-US"}, "language"),
        ({"required_format": SpeechAudioFormat.MP3}, "format"),
        ({"required_sample_rate_hz": 16_000}, "sample rate"),
        ({"required_channel_count": 8}, "channel count"),
        ({"normalized_text_characters": 1_001}, "character limit"),
        ({"normalized_text_bytes": 4_001}, "byte limit"),
        ({"requested_speaking_rate": Decimal("3")}, "speaking rate"),
        ({"required_style": "angry"}, "style"),
        ({"require_character_timing": True}, "character-level"),
        (
            {"generation_mode": SpeechRemoteGenerationMode.STREAMING},
            "generation mode",
        ),
    ],
)
def test_selection_rejects_unsupported_requirements(
    updates: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(SpeechVoiceSelectionError, match=message):
        SpeechVoiceSelector().select(
            snapshot=capability_snapshot(),
            request=selection_request(**updates),
        )


def test_voice_fallback_requires_explicit_model_default_policy() -> None:
    snapshot = capability_snapshot()
    with pytest.raises(SpeechVoiceSelectionError, match="fallback"):
        SpeechVoiceSelector().select(
            snapshot=snapshot,
            request=selection_request(requested_voice=None),
        )

    selected = SpeechVoiceSelector().select(
        snapshot=snapshot,
        request=selection_request(
            requested_voice=None,
            requested_speaking_rate=None,
            fallback_policy=SpeechVoiceFallbackPolicy.EXPLICIT_MODEL_DEFAULT,
        ),
    )
    assert selected.voice == "fallback-voice"
    assert selected.selection_reason == "explicit model default"


def test_unknown_pricing_fails_when_selection_has_budget_ceiling() -> None:
    from backend.src.production.speech_generation.remote_capabilities import (
        SpeechPricingUnit,
    )

    with pytest.raises(SpeechVoiceSelectionError, match="pricing"):
        SpeechVoiceSelector().select(
            snapshot=capability_snapshot(pricing_unit=SpeechPricingUnit.UNKNOWN),
            request=selection_request(budget_ceiling=Decimal("1")),
        )
