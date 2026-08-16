"""Model-specific OpenRouter speech contracts."""

from __future__ import annotations

from typing import Final

KOKORO_MODEL: Final = "hexgrad/kokoro-82m"
GEMINI_FLASH_TTS_MODEL: Final = "google/gemini-3.1-flash-tts-preview"

# Google documents these prebuilt Gemini TTS voices for single-speaker output.
GEMINI_TTS_VOICES: Final = frozenset(
    {
        "Zephyr",
        "Puck",
        "Charon",
        "Kore",
        "Fenrir",
        "Leda",
        "Orus",
        "Aoede",
        "Callirrhoe",
        "Autonoe",
        "Enceladus",
        "Iapetus",
        "Umbriel",
        "Algieba",
        "Despina",
        "Erinome",
        "Algenib",
        "Rasalgethi",
        "Laomedeia",
        "Achernar",
        "Alnilam",
        "Schedar",
        "Gacrux",
        "Pulcherrima",
        "Achird",
        "Zubenelgenubi",
        "Vindemiatrix",
        "Sadachbia",
        "Sadaltager",
        "Sulafat",
    }
)


def validate_model_voice(*, model: str, voice: str) -> None:
    """Reject known model/voice mismatches before any billable request."""

    if model == GEMINI_FLASH_TTS_MODEL and voice not in GEMINI_TTS_VOICES:
        raise ValueError(
            "Gemini Flash TTS voice is unsupported; use one of the documented "
            "Google prebuilt voices"
        )


def build_model_input(*, model: str, text: str, style_prompt: str | None) -> str:
    """Build the model input without changing Kokoro's transcript contract."""

    if model != GEMINI_FLASH_TTS_MODEL or not style_prompt:
        return text
    return (
        "Synthesize speech only. Do not read these instructions aloud.\n\n"
        f"Director's notes:\n{style_prompt.strip()}\n\n"
        f"Transcript:\n{text}"
    )
