from __future__ import annotations

import json
from collections import deque
from decimal import Decimal
from pathlib import Path

from backend.src.production.application.results import StageOutcome
from backend.src.production.speech_generation.handler import SpeechGenerationHandler
from backend.src.production.speech_generation.manifest_writer import (
    InMemorySpeechManifestWriter,
)
from backend.src.production.speech_generation.models import SpeechSegmentAudioMetadata
from backend.src.production.speech_generation.narration_fitting import (
    NarrationFittingConfiguration,
    NarrationFittingProviderError,
    NarrationFittingRequest,
    NarrationFittingResult,
    NarrationFittingStatus,
)
from backend.src.production.speech_generation.ports import SpeechProviderResult
from backend.src.production.speech_generation.providers.simulated_provider import _render_wav
from backend.tests.unit.production.speech_generation.conftest import (
    NOW,
    FakeSourceReader,
    audio_store,
    command_context,
    source_script,
    speech_configuration,
)

ORIGINAL_ONE = (
    "En las profundidades del océano, luces extrañas revelan criaturas antiguas "
    "que sobreviven sin recibir jamás la luz del sol."
)
ORIGINAL_TWO = (
    "Bajo otra fosa remota, sonidos imposibles atraviesan kilómetros de agua y "
    "todavía desconciertan a quienes intentan explicar su origen."
)
REVISION_ONE = "Luces extrañas revelan criaturas antiguas en las profundidades."
REVISION_TWO = "Sonidos imposibles cruzan fosas remotas y aún desconciertan a los científicos."
REVISION_TWO_SHORT = "Sonidos misteriosos cruzan fosas remotas sin explicación."


class SequencedSpeechProvider:
    name = "openrouter"

    def __init__(self, durations_ms: tuple[int, ...], *, fail_on: int | None = None) -> None:
        self.durations = deque(durations_ms)
        self.calls = 0
        self.fail_on = fail_on

    async def generate(self, request):
        self.calls += 1
        if self.calls == self.fail_on:
            from backend.src.production.speech_generation.exceptions import (
                SpeechProviderResponseError,
            )

            raise SpeechProviderResponseError("fake speech failure")
        duration_ms = self.durations.popleft()
        frames = round(duration_ms * request.configuration.sample_rate_hz / 1_000)
        return SpeechProviderResult(
            content=_render_wav(
                request.segment.normalized_text_hash,
                sample_rate_hz=request.configuration.sample_rate_hz,
                frame_count=frames,
            ),
            provider="openrouter",
            audio=SpeechSegmentAudioMetadata(
                duration_ms=duration_ms,
                sample_rate_hz=request.configuration.sample_rate_hz,
                frame_count=frames,
            ),
            deterministic=False,
            metadata={"network": False},
        )

    async def close(self) -> None:
        return None


class FakeNarrationFitter:
    name = "openrouter"
    model = "google/gemini-2.5-flash-lite"

    def __init__(self, revisions: dict[tuple[str, int], str]) -> None:
        self.revisions = revisions
        self.calls: list[tuple[str, int]] = []

    async def revise(self, request: NarrationFittingRequest) -> NarrationFittingResult:
        key = (request.scene_id, request.attempt_number)
        self.calls.append(key)
        return NarrationFittingResult(
            revised_narration=self.revisions[key],
            provider=self.name,
            model=self.model,
            http_status=200,
            provider_request_id=f"fit-{request.scene_id}-{request.attempt_number}",
            input_tokens=40,
            output_tokens=20,
            total_tokens=60,
            reported_cost_usd=Decimal("0.0001"),
            finish_reason="stop",
        )

    async def close(self) -> None:
        return None


def _source():
    source = source_script(first_narration=ORIGINAL_ONE)
    scenes = (
        source.script.scenes[0].model_copy(
            update={"narration": ORIGINAL_ONE, "estimated_duration_seconds": 4.0}
        ),
        source.script.scenes[1].model_copy(
            update={"narration": ORIGINAL_TWO, "estimated_duration_seconds": 4.0}
        ),
    )
    return source.model_copy(
        update={
            "script": source.script.model_copy(
                update={"target_duration_seconds": 8.0, "scenes": scenes}
            )
        }
    )


def _fitting_configuration(*, maximum_attempts: int = 2) -> NarrationFittingConfiguration:
    return NarrationFittingConfiguration(
        provider="openrouter",
        model="google/gemini-2.5-flash-lite",
        allow_billable_requests=True,
        maximum_attempts=maximum_attempts,
        estimated_cost_usd_per_attempt=Decimal("0.001"),
        maximum_estimated_cost_usd_per_attempt=Decimal("0.002"),
        maximum_estimated_job_cost_usd=Decimal("0.008"),
    )


def _handler(
    tmp_path: Path,
    *,
    speech,
    fitter=None,
    writer=None,
    fitting_configuration=None,
    fitting_recovery_store=None,
    source=None,
):
    configuration = speech_configuration(
        provider="openrouter",
        max_segment_duration_ms=8_000,
        max_audio_bytes=500_000,
    )
    return SpeechGenerationHandler(
        script_reader=FakeSourceReader(source or _source()),
        provider=speech,
        audio_store=audio_store(tmp_path, configuration),
        manifest_writer=writer or InMemorySpeechManifestWriter(),
        configuration=configuration,
        clock=lambda: NOW,
        narration_fitter=fitter,
        narration_fitting_configuration=fitting_configuration,
        fitting_recovery_store=fitting_recovery_store,
    )


async def test_case_a_no_fitting_needed(tmp_path: Path) -> None:
    speech = SequencedSpeechProvider((4_000, 4_000))
    fitter = FakeNarrationFitter({})
    command, context = command_context()

    output = await _handler(tmp_path, speech=speech, fitter=fitter).execute(command, context)

    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert fitter.calls == []
    assert speech.calls == 2


async def test_real_tts_underflow_fails_without_post_tts_billable_loop(
    tmp_path: Path,
) -> None:
    speech = SequencedSpeechProvider((2_800, 2_800))
    fitter = FakeNarrationFitter({})
    writer = InMemorySpeechManifestWriter()
    command, context = command_context()

    output = await _handler(
        tmp_path,
        speech=speech,
        fitter=fitter,
        writer=writer,
    ).execute(command, context)
    manifest = await writer.read_existing(context=context)

    assert output.result.outcome is StageOutcome.FAILED_PERMANENT
    assert output.result.error_code == "narration_duration_underflow"
    assert speech.calls == 2
    assert fitter.calls == []
    assert manifest is not None
    assert manifest.duration_occupancy is not None
    assert manifest.duration_occupancy.status.value == "too_short"


async def test_real_tts_inside_occupancy_window_skips_fitting(tmp_path: Path) -> None:
    speech = SequencedSpeechProvider((3_700, 3_800))
    fitter = FakeNarrationFitter({})
    writer = InMemorySpeechManifestWriter()
    command, context = command_context()

    output = await _handler(
        tmp_path,
        speech=speech,
        fitter=fitter,
        writer=writer,
    ).execute(command, context)
    manifest = await writer.read_existing(context=context)

    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert fitter.calls == []
    assert manifest is not None
    assert manifest.duration_occupancy is not None
    assert manifest.duration_occupancy.status.value == "acceptable"


async def test_case_b_fitting_succeeds_on_first_attempt(tmp_path: Path) -> None:
    speech = SequencedSpeechProvider((5_325, 5_975, 4_200))
    fitter = FakeNarrationFitter({("scene-001", 1): REVISION_ONE, ("scene-002", 1): REVISION_TWO})
    writer = InMemorySpeechManifestWriter()
    command, context = command_context()

    output = await _handler(
        tmp_path,
        speech=speech,
        fitter=fitter,
        writer=writer,
        fitting_configuration=_fitting_configuration(),
    ).execute(command, context)
    manifest = await writer.read_existing(context=context)

    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert fitter.calls == [("scene-002", 1)]
    assert manifest is not None and manifest.duration_resolution is not None
    assert manifest.duration_resolution.resolved_duration_ms == 9_525
    assert tuple(record.status for record in manifest.fitting_records) == (
        NarrationFittingStatus.COMPLETED,
    )


async def test_case_c_second_attempt_only_regenerates_remaining_scene(
    tmp_path: Path,
) -> None:
    speech = SequencedSpeechProvider((5_325, 5_975, 5_400, 4_200))
    fitter = FakeNarrationFitter(
        {
            ("scene-002", 1): REVISION_TWO,
            ("scene-002", 2): REVISION_TWO_SHORT,
        }
    )
    writer = InMemorySpeechManifestWriter()
    command, context = command_context()

    output = await _handler(
        tmp_path,
        speech=speech,
        fitter=fitter,
        writer=writer,
        fitting_configuration=_fitting_configuration(),
    ).execute(command, context)
    manifest = await writer.read_existing(context=context)

    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert fitter.calls == [
        ("scene-002", 1),
        ("scene-002", 2),
    ]
    assert speech.calls == 4
    assert manifest is not None and manifest.duration_resolution is not None
    assert manifest.duration_resolution.resolved_duration_ms == 9_525
    assert manifest.entries[0].fitting_revision == 0
    assert manifest.entries[1].fitting_revision == 2


async def test_case_d_exhaustion_blocks_video_handoff(tmp_path: Path) -> None:
    speech = SequencedSpeechProvider((5_325, 5_975, 5_500, 5_000))
    fitter = FakeNarrationFitter(
        {
            ("scene-002", 1): REVISION_TWO,
            ("scene-002", 2): REVISION_TWO_SHORT,
        }
    )
    command, context = command_context()
    video_posts = 0

    output = await _handler(
        tmp_path,
        speech=speech,
        fitter=fitter,
        fitting_configuration=_fitting_configuration(),
    ).execute(command, context)

    assert output.result.outcome is StageOutcome.FAILED_PERMANENT
    assert output.result.error_code == "narration_duration_overflow"
    assert video_posts == 0


async def test_recovery_reuses_completed_fitting_and_unmodified_images(
    tmp_path: Path,
) -> None:
    first_speech = SequencedSpeechProvider((5_325, 5_975, 4_400), fail_on=3)
    fitter = FakeNarrationFitter({("scene-001", 1): REVISION_ONE, ("scene-002", 1): REVISION_TWO})
    writer = InMemorySpeechManifestWriter()
    command, context = command_context()
    first_handler = _handler(
        tmp_path,
        speech=first_speech,
        fitter=fitter,
        writer=writer,
        fitting_configuration=_fitting_configuration(),
    )

    first = await first_handler.execute(command, context)
    assert first.result.error_code == "speech_segment_generation_failed"
    assert fitter.calls == [("scene-002", 1)]

    retry_speech = SequencedSpeechProvider((4_200,))
    image_provider_calls = 0
    second = await _handler(
        tmp_path,
        speech=retry_speech,
        fitter=fitter,
        writer=writer,
        fitting_configuration=_fitting_configuration(),
    ).execute(command, context)

    assert second.result.outcome is StageOutcome.SUCCEEDED
    assert fitter.calls == [("scene-002", 1)]
    assert retry_speech.calls == 1
    assert image_provider_calls == 0


async def test_historical_manifest_without_fitting_fields_remains_readable(
    tmp_path: Path,
) -> None:
    writer = InMemorySpeechManifestWriter()
    command, context = command_context()
    output = await _handler(
        tmp_path,
        speech=SequencedSpeechProvider((4_000, 4_000)),
        writer=writer,
    ).execute(command, context)
    assert output.result.outcome is StageOutcome.SUCCEEDED
    manifest = await writer.read_existing(context=context)
    assert manifest is not None
    payload = manifest.model_dump(mode="json")
    payload.pop("fitting_records")
    payload.pop("duration_occupancy")
    for entry in payload["entries"]:
        entry.pop("source_segment_id")
        entry.pop("fitting_revision")

    from backend.src.production.speech_generation.models import SpeechGenerationManifest

    loaded = SpeechGenerationManifest.model_validate(json.loads(json.dumps(payload)))
    assert loaded.fitting_records == ()
    assert loaded.duration_occupancy is None
    assert all(entry.fitting_revision == 0 for entry in loaded.entries)


class DiagnosticFailureFitter:
    name = "openrouter"
    model = "google/gemini-2.5-flash-lite"

    async def revise(self, request: NarrationFittingRequest) -> NarrationFittingResult:
        raise NarrationFittingProviderError(
            "fake timeout",
            safe_error_code="timeout",
            retryable=True,
            provider_retry_count=1,
        )

    async def close(self) -> None:
        return None


async def test_provider_diagnostics_are_persisted_without_secrets(tmp_path: Path) -> None:
    writer = InMemorySpeechManifestWriter()
    command, context = command_context()
    output = await _handler(
        tmp_path,
        speech=SequencedSpeechProvider((5_325, 5_100)),
        fitter=DiagnosticFailureFitter(),
        writer=writer,
        fitting_configuration=_fitting_configuration(),
    ).execute(command, context)
    manifest = await writer.read_existing(context=context)
    assert output.result.error_code == "narration_fitting_provider_error"
    assert manifest is not None
    record = manifest.fitting_records[0]
    assert record.safe_error_code == "timeout"
    assert record.retryable is True
    assert record.provider_retry_count == 1
    assert record.response_received is False
    assert record.provider_request_id is None
