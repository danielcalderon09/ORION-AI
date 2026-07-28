"""Provider-neutral deterministic simulated duration calculation."""

import re

from backend.src.production.speech_generation.ports import SpeechProviderRequest

_WORDS = re.compile(r"\b[\wÀ-ÿ]+\b", re.UNICODE)
_PUNCTUATION = re.compile(r"[,;:.!?]")


def simulated_duration_ms(request: SpeechProviderRequest) -> int:
    configuration = request.configuration
    target = request.segment.target_duration_ms
    if target is None:
        text = request.segment.narration_text
        words = len(_WORDS.findall(text))
        punctuation = len(_PUNCTUATION.findall(text))
        target = round(words * 60_000 / configuration.words_per_minute)
        target += punctuation * 120
    return min(
        configuration.max_segment_duration_ms,
        max(configuration.min_duration_ms, target),
    )
