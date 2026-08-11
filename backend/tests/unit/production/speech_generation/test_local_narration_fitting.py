from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

from backend.src.production.application.results import StageOutcome
from backend.src.production.speech_generation.fitting_recovery import (
    speech_manifest_sha256,
)
from backend.src.production.speech_generation.local_narration_fitter import (
    DeterministicSpanishNarrationFitter,
)
from backend.src.production.speech_generation.manifest_writer import (
    InMemorySpeechManifestWriter,
)
from backend.src.production.speech_generation.narration_fitting import (
    NarrationFittingRequest,
    NarrationFittingStrategy,
)
from backend.tests.unit.production.speech_generation.conftest import (
    command_context,
    source_script,
)
from backend.tests.unit.production.speech_generation.test_narration_fitting import (
    FakeNarrationFitter,
    SequencedSpeechProvider,
    _fitting_configuration,
    _handler,
)

LOCAL_SOURCE = "La materia se comprime hasta un punto de densidad infinita, creando un agujero negro."
LOCAL_CANDIDATE = "La materia se comprime hasta densidad infinita, creando un agujero negro."
UNCHANGED_SCENE = "Estrella masiva agota combustible e inicia un colapso gravitacional imparable."
REMOTE_REVISION = "La materia alcanza densidad infinita y crea un agujero negro."


def _request(
    narration: str,
    *,
    current_duration_ms: int = 5_100,
    target_duration_ms: int = 4_000,
) -> NarrationFittingRequest:
    command, _ = command_context()
    return NarrationFittingRequest(
        job_id=command.job_id,
        scene_id="scene-002",
        sequence_index=1,
        attempt_number=1,
        current_narration=narration,
        current_duration_ms=current_duration_ms,
        target_duration_ms=target_duration_ms,
        language="es-ES",
        tone="cinematográfico",
    )


def _local_source():
    source = source_script(first_narration=UNCHANGED_SCENE)
    scenes = (
        source.script.scenes[0].model_copy(
            update={"narration": UNCHANGED_SCENE, "estimated_duration_seconds": 4.0}
        ),
        source.script.scenes[1].model_copy(
            update={"narration": LOCAL_SOURCE, "estimated_duration_seconds": 4.0}
        ),
    )
    return source.model_copy(
        update={
            "script": source.script.model_copy(
                update={"target_duration_seconds": 8.0, "scenes": scenes}
            )
        }
    )


def test_small_spanish_overrun_is_fitted_deterministically() -> None:
    fitter = DeterministicSpanishNarrationFitter()

    first = fitter.revise(_request(LOCAL_SOURCE))
    second = fitter.revise(_request(LOCAL_SOURCE))

    assert first == second
    assert first is not None
    assert first.revised_narration == LOCAL_CANDIDATE
    assert first.rules_applied == ("threshold_hasta",)
    assert all(
        term in first.revised_narration
        for term in ("materia", "densidad", "agujero negro")
    )


def test_local_fitting_preserves_named_entities_negation_and_numbers() -> None:
    source = (
        "Marie Curie no fue capaz de ignorar 2 muestras porque se encontraba "
        "en el momento en que cambiaron."
    )
    result = DeterministicSpanishNarrationFitter().revise(_request(source))

    assert result is not None
    assert "Marie Curie" in result.revised_narration
    assert " no " in f" {result.revised_narration} "
    assert "2" in result.revised_narration


def test_large_overrun_is_not_applicable() -> None:
    assert (
        DeterministicSpanishNarrationFitter().revise(
            _request(LOCAL_SOURCE, current_duration_ms=6_000)
        )
        is None
    )


async def test_local_success_skips_remote_and_persists_zero_cost_record(
    tmp_path: Path,
) -> None:
    speech = SequencedSpeechProvider((4_800, 5_100, 4_500))
    remote = FakeNarrationFitter({})
    writer = InMemorySpeechManifestWriter()
    command, context = command_context()

    output = await _handler(
        tmp_path,
        speech=speech,
        fitter=remote,
        writer=writer,
        fitting_configuration=_fitting_configuration(),
        source=_local_source(),
    ).execute(command, context)
    manifest = await writer.read_existing(context=context)

    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert remote.calls == []
    assert speech.calls == 3
    assert manifest is not None and manifest.duration_resolution is not None
    assert manifest.duration_resolution.resolved_duration_ms == 9_300
    record = manifest.fitting_records[0]
    assert record.strategy is NarrationFittingStrategy.DETERMINISTIC_LOCAL
    assert record.provider == "deterministic_local"
    assert record.estimated_cost_usd == Decimal(0)
    assert record.maximum_authorized_cost_usd == Decimal(0)
    assert record.provider_request_id is None
    assert record.rules_applied == ("threshold_hasta",)


async def test_local_success_does_not_require_remote_fitting_authorization(
    tmp_path: Path,
) -> None:
    remote = FakeNarrationFitter({})
    output = await _handler(
        tmp_path,
        speech=SequencedSpeechProvider((4_800, 5_100, 4_500)),
        fitter=remote,
        source=_local_source(),
    ).execute(*command_context())

    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert remote.calls == []


async def test_local_candidate_still_long_falls_back_to_remote_same_round(
    tmp_path: Path,
) -> None:
    speech = SequencedSpeechProvider((4_800, 5_100, 4_900, 4_500))
    remote = FakeNarrationFitter({("scene-002", 1): REMOTE_REVISION})
    writer = InMemorySpeechManifestWriter()
    command, context = command_context()

    output = await _handler(
        tmp_path,
        speech=speech,
        fitter=remote,
        writer=writer,
        fitting_configuration=_fitting_configuration(),
        source=_local_source(),
    ).execute(command, context)
    manifest = await writer.read_existing(context=context)

    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert remote.calls == [("scene-002", 1)]
    assert speech.calls == 4
    assert manifest is not None
    assert tuple(record.strategy for record in manifest.fitting_records) == (
        NarrationFittingStrategy.DETERMINISTIC_LOCAL,
        NarrationFittingStrategy.REMOTE_PROVIDER,
    )
    assert manifest.entries[1].fitting_revision == 2


async def test_not_applicable_local_fitting_uses_remote(tmp_path: Path) -> None:
    speech = SequencedSpeechProvider((4_000, 6_000, 4_500))
    remote = FakeNarrationFitter({("scene-002", 1): REMOTE_REVISION})
    command, _ = command_context()

    output = await _handler(
        tmp_path,
        speech=speech,
        fitter=remote,
        fitting_configuration=_fitting_configuration(),
        source=_local_source(),
    ).execute(*command_context())

    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert remote.calls == [("scene-002", 1)]
    assert command.job_id == output.artifacts[0].job_id


async def test_completed_local_fit_is_reused_after_tts_interruption(tmp_path: Path) -> None:
    writer = InMemorySpeechManifestWriter()
    remote = FakeNarrationFitter({})
    command, context = command_context()
    first = await _handler(
        tmp_path,
        speech=SequencedSpeechProvider((4_800, 5_100), fail_on=3),
        fitter=remote,
        writer=writer,
        fitting_configuration=_fitting_configuration(),
        source=_local_source(),
    ).execute(command, context)
    assert first.result.error_code == "speech_segment_generation_failed"

    retry_speech = SequencedSpeechProvider((4_500,))
    second = await _handler(
        tmp_path,
        speech=retry_speech,
        fitter=remote,
        writer=writer,
        fitting_configuration=_fitting_configuration(),
        source=_local_source(),
    ).execute(command, context)
    manifest = await writer.read_existing(context=context)

    assert second.result.outcome is StageOutcome.SUCCEEDED
    assert remote.calls == []
    assert retry_speech.calls == 1
    assert manifest is not None
    assert len(manifest.fitting_records) == 1


async def test_historical_remote_record_defaults_remain_readable(tmp_path: Path) -> None:
    writer = InMemorySpeechManifestWriter()
    command, context = command_context()
    remote = FakeNarrationFitter({("scene-002", 1): REMOTE_REVISION})
    await _handler(
        tmp_path,
        speech=SequencedSpeechProvider((4_000, 6_000, 4_500)),
        fitter=remote,
        writer=writer,
        fitting_configuration=_fitting_configuration(),
        source=_local_source(),
    ).execute(command, context)
    manifest = await writer.read_existing(context=context)
    assert manifest is not None
    payload = manifest.model_dump(mode="json")
    for record in payload["fitting_records"]:
        record.pop("strategy")
        record.pop("rules_applied")

    from backend.src.production.speech_generation.models import SpeechGenerationManifest

    historical_sha = hashlib.sha256(
        (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
    ).hexdigest()
    loaded = SpeechGenerationManifest.model_validate(json.loads(json.dumps(payload)))
    assert all(
        record.strategy is NarrationFittingStrategy.REMOTE_PROVIDER
        for record in loaded.fitting_records
    )
    assert speech_manifest_sha256(loaded) == historical_sha
