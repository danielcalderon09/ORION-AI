"""Offline tests for model-aware OpenRouter speech profiles."""

from decimal import Decimal

import pytest

from backend.src.production.speech_generation.model_profiles import (
    GEMINI_FLASH_TTS_MODEL,
    GEMINI_TTS_VOICES,
    build_model_input,
    validate_model_voice,
)
from backend.src.production.speech_generation.providers.openrouter_provider import (
    OpenRouterSpeechGenerationProvider,
)
from backend.src.production.speech_generation.remote_job_store import (
    InMemoryRemoteSpeechJobStore,
)


def test_kokoro_remains_supported_and_gemini_has_separate_voice_contract() -> None:
    validate_model_voice(model="hexgrad/kokoro-82m", voice="em_alex")
    validate_model_voice(model=GEMINI_FLASH_TTS_MODEL, voice="Kore")
    assert "Kore" in GEMINI_TTS_VOICES
    with pytest.raises(ValueError, match="Gemini Flash TTS voice"):
        validate_model_voice(model=GEMINI_FLASH_TTS_MODEL, voice="em_alex")


def test_gemini_style_prompt_is_director_input_and_kokoro_is_unchanged() -> None:
    style = "Spanish Latin American narrator; natural, confident and cinematic."
    gemini_input = build_model_input(
        model=GEMINI_FLASH_TTS_MODEL,
        text="Texto de prueba.",
        style_prompt=style,
    )
    assert "Do not read these instructions aloud" in gemini_input
    assert style in gemini_input
    assert "Transcript:\nTexto de prueba." in gemini_input
    assert build_model_input(
        model="hexgrad/kokoro-82m",
        text="Texto de prueba.",
        style_prompt=style,
    ) == "Texto de prueba."


def test_gemini_selection_is_fail_closed_before_transport() -> None:
    with pytest.raises(ValueError, match="Gemini Flash TTS voice"):
        OpenRouterSpeechGenerationProvider(
            api_key="fake-key",
            model=GEMINI_FLASH_TTS_MODEL,
            voice="em_alex",
            estimated_cost_usd=Decimal("0.01"),
            maximum_authorized_cost_usd=Decimal("0.01"),
            allow_billable_requests=True,
            remote_job_store=InMemoryRemoteSpeechJobStore(),
        )
