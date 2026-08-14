from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from backend.src.production.application.results import StageOutcome
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.speech_generation.fitting_recovery import (
    FilesystemNarrationFittingRecoveryAuthorizationStore,
    NarrationFittingRecoveryAuthorizationError,
)
from backend.src.production.speech_generation.manifest_writer import (
    LocalSpeechManifestWriter,
)
from backend.src.production.speech_generation.narration_fitting import (
    NarrationFittingProviderError,
    NarrationFittingRequest,
    NarrationFittingResult,
)
from backend.src.production.speech_generation.segment_builder import build_speech_segments
from backend.src.production.speech_generation.serialization import (
    serialize_speech_manifest,
)
from backend.tests.unit.production.speech_generation.conftest import (
    JOB_ID,
    NOW,
    command_context,
    speech_configuration,
)
from backend.tests.unit.production.speech_generation.test_narration_fitting import (
    REVISION_ONE,
    REVISION_TWO_SHORT,
    FakeNarrationFitter,
    SequencedSpeechProvider,
    _fitting_configuration,
    _handler,
    _source,
)


class FailSecondSceneFitter:
    name = "openrouter"
    model = "google/gemini-2.5-flash-lite"

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def revise(self, request: NarrationFittingRequest) -> NarrationFittingResult:
        self.calls.append((request.scene_id, request.attempt_number))
        if request.scene_id == "scene-002":
            raise NarrationFittingProviderError(
                "temporary provider failure",
                safe_error_code="provider_error",
                retryable=True,
            )
        return NarrationFittingResult(
            revised_narration=REVISION_ONE,
            provider=self.name,
            model=self.model,
            http_status=200,
            provider_request_id="fit-scene-001-1",
            reported_cost_usd=Decimal("0.0000202"),
            finish_reason="stop",
        )

    async def close(self) -> None:
        return None


async def _failed_reference_equivalent(tmp_path: Path):
    configuration = _fitting_configuration().model_copy(
        update={"maximum_estimated_job_cost_usd": Decimal("0.002")}
    )
    writer = LocalSpeechManifestWriter(tmp_path, max_manifest_bytes=2_000_000)
    fitter = FailSecondSceneFitter()
    command, context = command_context()
    output = await _handler(
        tmp_path,
        # Both overruns are required to recover the global excess; the second
        # fitting request therefore remains the durable failure boundary.
        speech=SequencedSpeechProvider((6_225, 5_700)),
        fitter=fitter,
        writer=writer,
        fitting_configuration=configuration,
    ).execute(command, context)
    assert output.result.outcome is StageOutcome.FAILED_PERMANENT
    assert output.result.error_code == "narration_fitting_provider_error"
    source = await writer.read_existing(context=context)
    assert source is not None
    return configuration, writer, fitter, source


async def _authorize(
    tmp_path: Path,
    budget: str,
    *,
    settings_budget: str = "0.004",
    maximum_fitting_attempts: int = 2,
):
    store = FilesystemNarrationFittingRecoveryAuthorizationStore(tmp_path)
    authorization = await store.authorize(
        job_id=JOB_ID,
        job_status=ProductionJobStatus.FAILED,
        current_stage=ProductionStage.GENERATING_NARRATION,
        new_authorized_job_cost_usd=Decimal(budget),
        current_settings_authorization_usd=Decimal(settings_budget),
        estimated_cost_per_provider_request_usd=Decimal("0.001"),
        maximum_fitting_attempts=maximum_fitting_attempts,
        maximum_provider_retries=1,
        clock=lambda: NOW + timedelta(hours=1),
    )
    return store, authorization


async def _build_failed_attempt_2(tmp_path: Path):
    configuration, writer, first_fitter, source = await _failed_reference_equivalent(
        tmp_path
    )
    store, authorization = await _authorize(tmp_path, "0.004")
    fitter = FakeNarrationFitter({("scene-002", 2): REVISION_TWO_SHORT})
    second_writer = LocalSpeechManifestWriter(tmp_path, max_manifest_bytes=2_000_000)
    command, context = command_context(attempt=2)
    output = await _handler(
        tmp_path,
        speech=SequencedSpeechProvider((5_475, 4_800)),
        fitter=fitter,
        writer=second_writer,
        fitting_configuration=configuration.model_copy(
            update={"maximum_estimated_job_cost_usd": Decimal("0.004")}
        ),
        fitting_recovery_store=store,
    ).execute(command, context)
    assert output.result.error_code == "narration_fitting_exhausted"
    manifest = await second_writer.read_existing(context=context)
    assert manifest is not None
    return configuration, first_fitter, fitter, source, manifest


async def test_recovery_authorization_004_adds_two_request_capacity(tmp_path: Path) -> None:
    await _failed_reference_equivalent(tmp_path)

    store, (authorization, idempotent) = await _authorize(tmp_path, "0.004")

    assert idempotent is False
    assert authorization.previous_authorized_job_cost_usd == Decimal("0.002")
    assert authorization.existing_committed_estimate_usd == Decimal("0.002")
    assert authorization.additional_authorized_cost_usd == Decimal("0.002")
    assert authorization.maximum_additional_provider_requests == 2
    assert await store.read(job_id=JOB_ID) == authorization


async def test_recovery_authorization_rejects_no_increase(tmp_path: Path) -> None:
    await _failed_reference_equivalent(tmp_path)

    with pytest.raises(
        NarrationFittingRecoveryAuthorizationError,
        match="must exceed historical authorization",
    ):
        await _authorize(tmp_path, "0.002")


async def test_recovery_authorization_003_allows_one_request(tmp_path: Path) -> None:
    await _failed_reference_equivalent(tmp_path)

    _, (authorization, _) = await _authorize(tmp_path, "0.003")

    assert authorization.additional_authorized_cost_usd == Decimal("0.001")
    assert authorization.maximum_additional_provider_requests == 1


async def test_recovery_authorization_rejects_above_current_settings(tmp_path: Path) -> None:
    await _failed_reference_equivalent(tmp_path)

    with pytest.raises(
        NarrationFittingRecoveryAuthorizationError,
        match="exceeds current Settings authorization",
    ):
        await _authorize(tmp_path, "0.005")


async def test_recovery_authorization_rejects_exhausted_fitting_policy(
    tmp_path: Path,
) -> None:
    await _failed_reference_equivalent(tmp_path)

    with pytest.raises(
        NarrationFittingRecoveryAuthorizationError,
        match="attempt policy is exhausted",
    ):
        await _authorize(tmp_path, "0.004", maximum_fitting_attempts=1)


async def test_recovery_authorization_is_idempotent(tmp_path: Path) -> None:
    await _failed_reference_equivalent(tmp_path)
    _, (first, first_idempotent) = await _authorize(tmp_path, "0.004")

    _, (second, second_idempotent) = await _authorize(tmp_path, "0.004")

    assert first_idempotent is False
    assert second_idempotent is True
    assert second == first


async def test_source_manifest_drift_fails_closed(tmp_path: Path) -> None:
    _, writer, _, source = await _failed_reference_equivalent(tmp_path)
    store, (authorization, _) = await _authorize(tmp_path, "0.004")
    _, context = command_context()
    drifted = source.model_copy(update={"updated_at": NOW + timedelta(days=1)})
    written = await writer.written(context=context)
    target = tmp_path / written.relative_path
    target.write_bytes(serialize_speech_manifest(drifted))

    with pytest.raises(
        NarrationFittingRecoveryAuthorizationError,
        match="fingerprint drifted",
    ):
        await store.load_source_manifest(authorization)


async def test_recovery_authorization_fingerprint_drift_fails_closed(
    tmp_path: Path,
) -> None:
    await _failed_reference_equivalent(tmp_path)
    store, _ = await _authorize(tmp_path, "0.004")
    target = (
        tmp_path
        / f"production/{JOB_ID}/generating_narration/"
        "narration-fitting-recovery-authorization.json"
    )
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["created_at"] = (NOW + timedelta(days=2)).isoformat()
    target.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        NarrationFittingRecoveryAuthorizationError,
        match="recovery authorization is invalid",
    ):
        await store.read(job_id=JOB_ID)


async def test_recovery_reuses_completed_scene_and_creates_next_attempt(
    tmp_path: Path,
) -> None:
    configuration, _, first_fitter, source = await _failed_reference_equivalent(tmp_path)
    source_bytes = (
        tmp_path
        / f"production/{JOB_ID}/generating_narration/attempt-1/"
        "speech-generation-manifest.json"
    ).read_bytes()
    store, (authorization, _) = await _authorize(tmp_path, "0.004")
    second_fitter = FakeNarrationFitter({("scene-002", 2): REVISION_TWO_SHORT})
    second_speech = SequencedSpeechProvider((5_000, 4_500))
    second_writer = LocalSpeechManifestWriter(tmp_path, max_manifest_bytes=2_000_000)
    command, context = command_context(attempt=2)

    second_handler = _handler(
        tmp_path,
        speech=second_speech,
        fitter=second_fitter,
        writer=second_writer,
        fitting_configuration=configuration.model_copy(
            update={"maximum_estimated_job_cost_usd": Decimal("0.004")}
        ),
        fitting_recovery_store=store,
    )
    speech_config = speech_configuration(
        provider="openrouter",
        max_segment_duration_ms=8_000,
        max_audio_bytes=500_000,
    )
    second_handler._recovery_manifest(
        command=command,
        source=_source(),
        segments=build_speech_segments(_source(), speech_config),
        source_manifest=source,
        authorization=authorization,
    )
    output = await second_handler.execute(command, context)
    recovered = await second_writer.read_existing(context=context)

    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert first_fitter.calls == [("scene-001", 1), ("scene-002", 1)]
    assert second_fitter.calls == [("scene-002", 2)]
    assert second_speech.calls == 2
    assert recovered is not None
    assert tuple((record.scene_id, record.attempt_number) for record in recovered.fitting_records) == (
        ("scene-001", 1),
        ("scene-002", 1),
        ("scene-002", 2),
    )
    assert recovered.duration_resolution is not None
    assert recovered.duration_resolution.resolved_duration_ms == 9_500
    assert source.fitting_records[1].status.value == "failed"
    assert (
        tmp_path
        / f"production/{JOB_ID}/generating_narration/attempt-1/"
        "speech-generation-manifest.json"
    ).read_bytes() == source_bytes


async def test_chained_authorization_targets_attempt_3_and_recovers_selectively(
    tmp_path: Path,
) -> None:
    configuration, first_fitter, second_fitter, attempt_1, attempt_2 = (
        await _build_failed_attempt_2(tmp_path)
    )
    store, (authorization_b, _) = await _authorize(
        tmp_path,
        "0.005",
        settings_budget="0.008",
        maximum_fitting_attempts=3,
    )
    assert authorization_b.source_attempt_number == 2
    assert authorization_b.maximum_additional_provider_requests == 2
    assert await store.read(job_id=JOB_ID, target_attempt_number=3) == authorization_b
    assert await store.read(job_id=JOB_ID, target_attempt_number=2) is not None
    _, (same_authorization, idempotent) = await _authorize(
        tmp_path,
        "0.005",
        settings_budget="0.008",
        maximum_fitting_attempts=3,
    )
    assert idempotent is True
    assert same_authorization == authorization_b

    attempt_1_path = (
        tmp_path
        / f"production/{JOB_ID}/generating_narration/attempt-1/"
        "speech-generation-manifest.json"
    )
    attempt_2_path = (
        tmp_path
        / f"production/{JOB_ID}/generating_narration/attempt-2/"
        "speech-generation-manifest.json"
    )
    attempt_1_bytes = attempt_1_path.read_bytes()
    attempt_2_bytes = attempt_2_path.read_bytes()

    third_fitter = FakeNarrationFitter({("scene-001", 3): REVISION_TWO_SHORT})
    third_writer = LocalSpeechManifestWriter(tmp_path, max_manifest_bytes=2_000_000)
    command, context = command_context(attempt=3)
    third_speech = SequencedSpeechProvider((4_400,))
    output = await _handler(
        tmp_path,
        speech=third_speech,
        fitter=third_fitter,
        writer=third_writer,
        fitting_configuration=configuration.model_copy(
            update={
                "maximum_estimated_job_cost_usd": Decimal("0.005"),
                "maximum_attempts": 3,
            }
        ),
        fitting_recovery_store=store,
    ).execute(command, context)
    attempt_3 = await third_writer.read_existing(context=context)

    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert third_fitter.calls == [("scene-001", 3)]
    assert third_fitter.calls.count(("scene-002", 3)) == 0
    assert third_speech.calls == 1
    assert attempt_3 is not None
    assert attempt_3.duration_resolution is not None
    assert attempt_3.duration_resolution.resolved_duration_ms == 9_200
    assert tuple(
        (record.scene_id, record.attempt_number)
        for record in attempt_3.fitting_records
    ) == (
        ("scene-001", 1),
        ("scene-002", 1),
        ("scene-002", 2),
        ("scene-001", 3),
    )
    assert attempt_1.fitting_records[1].status.value == "failed"
    assert attempt_2.fitting_records[-1].scene_id == "scene-002"
    assert attempt_1_path.read_bytes() == attempt_1_bytes
    assert attempt_2_path.read_bytes() == attempt_2_bytes
    assert first_fitter.calls == [("scene-001", 1), ("scene-002", 1)]
    assert second_fitter.calls == [("scene-002", 2)]


async def test_attempt_3_authorization_requires_settings_max_attempts_3(
    tmp_path: Path,
) -> None:
    await _build_failed_attempt_2(tmp_path)

    with pytest.raises(
        NarrationFittingRecoveryAuthorizationError,
        match="attempt policy is exhausted",
    ):
        await _authorize(
            tmp_path,
            "0.005",
            settings_budget="0.008",
            maximum_fitting_attempts=2,
        )


async def test_attempt_3_budget_004_allows_only_one_provider_request(
    tmp_path: Path,
) -> None:
    await _build_failed_attempt_2(tmp_path)

    store, (authorization, _) = await _authorize(
        tmp_path,
        "0.004",
        settings_budget="0.008",
        maximum_fitting_attempts=3,
    )

    assert authorization.existing_committed_estimate_usd == Decimal("0.003")
    assert authorization.additional_authorized_cost_usd == Decimal("0.001")
    assert authorization.maximum_additional_provider_requests == 1
    assert await store.read(job_id=JOB_ID, target_attempt_number=2) is not None


def test_cli_parser_accepts_safe_decimal() -> None:
    from backend.src.production.cli.authorize_narration_fitting_recovery import _parser

    args = _parser().parse_args(
        ["--job-id", str(JOB_ID), "--maximum-job-cost-usd", "0.004"]
    )
    assert args.maximum_job_cost_usd == Decimal("0.004")


def test_configuration_used_by_reference_fixture_is_openrouter() -> None:
    assert speech_configuration(provider="openrouter").provider == "openrouter"
