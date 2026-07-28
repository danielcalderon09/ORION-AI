"""Deterministic narration segment construction from the approved script."""

import hashlib
import re
import unicodedata

from backend.src.production.speech_generation.configuration import (
    SpeechGenerationConfiguration,
)
from backend.src.production.speech_generation.models import (
    SpeechSegmentRequest,
    SpeechTimingProvenance,
)
from backend.src.production.speech_generation.ports import ReadSpeechSourceScript

_WHITESPACE = re.compile(r"\s+")


def normalize_narration_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    return _WHITESPACE.sub(" ", normalized).strip()


def build_speech_segments(
    source: ReadSpeechSourceScript,
    configuration: SpeechGenerationConfiguration,
) -> tuple[SpeechSegmentRequest, ...]:
    language = source.script.language or configuration.language
    segments: list[SpeechSegmentRequest] = []
    for index, scene in enumerate(source.script.scenes):
        text = normalize_narration_text(scene.narration)
        if not text:
            raise ValueError("approved script narration cannot be empty")
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        target_duration_ms = round(scene.estimated_duration_seconds * 1_000)
        identity = "\0".join(
            (
                str(source.artifact_id),
                source.sha256,
                str(scene.scene_number),
                text_hash,
                configuration.voice,
                language.casefold(),
                str(configuration.words_per_minute),
                str(target_duration_ms),
            )
        )
        segment_id = f"segment-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"
        segments.append(
            SpeechSegmentRequest(
                segment_id=segment_id,
                source_script_artifact_id=source.artifact_id,
                source_script_sha256=source.sha256,
                scene_id=f"scene-{scene.scene_number:03d}",
                sequence_index=index,
                narration_text=text,
                normalized_text_hash=text_hash,
                requested_voice=configuration.voice,
                requested_language=language,
                requested_speaking_rate=configuration.words_per_minute,
                target_duration_ms=target_duration_ms,
                timing_provenance=SpeechTimingProvenance.SCRIPT_SCENE_ESTIMATE,
            )
        )
    return tuple(segments)
