"""Offline tests for one bounded job-scoped speech retry budget."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest

from backend.src.production.cost_accounting.durable_reader import (
    derive_durable_job_cost_summary,
)
from backend.src.production.cost_accounting.models import JobCostCategory
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.speech_generation.cost import SpeechReportedCost
from backend.src.production.speech_generation.exceptions import (
    SpeechCostLimitExhaustedError,
    SpeechRequestLimitExhaustedError,
)
from backend.src.production.speech_generation.providers.openrouter_provider import (
    OpenRouterSpeechGenerationProvider,
)
from backend.src.production.speech_generation.remote_job_store import (
    LocalRemoteSpeechJobStore,
)
from backend.src.production.speech_generation.remote_serialization import (
    serialize_remote_speech_job,
)
from backend.src.production.speech_generation.retry_budget import (
    FilesystemSpeechRetryBudgetAuthorizationStore,
    SpeechRetryBudgetAuthorization,
    SpeechRetryBudgetAuthorizationError,
    speech_retry_budget_fingerprint,
)
from backend.src.production.speech_generation.serialization import (
    serialize_speech_manifest,
)
from backend.tests.unit.production.speech_generation.test_storage_and_manifest import (
    _initial_manifest,
)
from backend.tests.unit.production.speech_generation.test_unresolved_replacement import (
    _request,
)

BASE_REQUESTS = 6
BASE_COST = Decimal("0.006")
PER_REQUEST = Decimal("0.001")


@pytest.mark.asyncio
async def test_bounded_overlay_allows_only_request_seven(tmp_path: Path) -> None:
    remote_store, retry_store, provider, calls, job_id = await _exhausted_job(tmp_path)
    scene_five = _job_request(job_id, attempt=7, ordinal=7)

    with pytest.raises(SpeechRequestLimitExhaustedError):
        await provider.generate(scene_five)
    assert calls[0] == 0
    assert len(await remote_store.list_records()) == 6
    before = _speech_cost(tmp_path, job_id)
    assert before.accounted_cost_usd == BASE_COST

    authorization, idempotent = await _authorize(retry_store, job_id)
    assert not idempotent
    assert authorization.new_maximum_requests_per_job == 7
    assert authorization.new_maximum_tts_job_cost_usd == Decimal("0.007")
    assert _speech_cost(tmp_path, job_id).accounted_cost_usd == BASE_COST

    await provider.generate(scene_five)
    assert calls[0] == 1
    records = await remote_store.list_records()
    assert len(records) == 7
    assert records[-1].segment_id == scene_five.segment.segment_id
    assert records[-1].status.value == "completed"
    after = _speech_cost(tmp_path, job_id)
    assert after.request_count == 7
    assert after.accounted_cost_usd == Decimal("0.007")

    with pytest.raises(SpeechRequestLimitExhaustedError):
        await provider.generate(_job_request(job_id, attempt=7, ordinal=8))
    assert calls[0] == 1
    assert len(await remote_store.list_records()) == 7
    await provider.close()


@pytest.mark.asyncio
async def test_cost_ceiling_rejects_locally_before_provider_post(tmp_path: Path) -> None:
    remote_store, _, provider, calls, job_id = await _exhausted_job(tmp_path)
    records = await remote_store.list_records()
    for record in records[1:]:
        _remote_record_path(tmp_path, job_id, record).unlink()
    first = records[0].model_copy(
        update={"reported_cost": SpeechReportedCost(currency="USD", amount=BASE_COST)}
    )
    _remote_record_path(tmp_path, job_id, first).write_bytes(
        serialize_remote_speech_job(first)
    )

    with pytest.raises(SpeechCostLimitExhaustedError):
        await provider.generate(_job_request(job_id, attempt=7, ordinal=7))
    assert calls[0] == 0
    assert len(await remote_store.list_records()) == 1
    await provider.close()


@pytest.mark.asyncio
async def test_authorization_is_idempotent_and_conflicts_fail_closed(
    tmp_path: Path,
) -> None:
    _, store, provider, _, job_id = await _exhausted_job(tmp_path)
    first, idempotent = await _authorize(store, job_id)
    duplicate, duplicate_idempotent = await _authorize(store, job_id)

    assert not idempotent
    assert duplicate_idempotent
    assert duplicate == first
    with pytest.raises(SpeechRetryBudgetAuthorizationError, match="conflicting"):
        await _authorize(store, job_id, operator_id="different-operator")
    await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("updates", "match"),
    [
        ({"additional_requests": 2, "new_requests": 8}, "invalid"),
        ({"additional_cost": Decimal("0.002")}, "invalid"),
        ({"new_requests": 6}, "invalid"),
        ({"new_cost": Decimal("0.006")}, "invalid"),
    ],
)
async def test_unbounded_or_inconsistent_capacity_is_rejected(
    tmp_path: Path,
    updates: dict[str, object],
    match: str,
) -> None:
    _, store, provider, _, job_id = await _exhausted_job(tmp_path)
    with pytest.raises(SpeechRetryBudgetAuthorizationError, match=match):
        await _authorize(store, job_id, **updates)
    await provider.close()


@pytest.mark.asyncio
async def test_wrong_job_and_malformed_fingerprint_fail_closed(tmp_path: Path) -> None:
    _, store, provider, _, job_id = await _exhausted_job(tmp_path)
    with pytest.raises(SpeechRetryBudgetAuthorizationError):
        await _authorize(store, uuid4())
    authorization, _ = await _authorize(store, job_id)
    path = _authorization_path(tmp_path, job_id)
    payload = json.loads(path.read_bytes())
    payload["fingerprint"] = "f" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SpeechRetryBudgetAuthorizationError, match="invalid"):
        await store.effective_limits(
            job_id=job_id,
            target_stage_attempt=authorization.target_stage_attempt,
            base_maximum_requests_per_job=BASE_REQUESTS,
            base_maximum_tts_job_cost_usd=BASE_COST,
            estimated_cost_per_request_usd=PER_REQUEST,
        )
    await provider.close()


@pytest.mark.asyncio
async def test_authorization_payload_for_another_job_fails_closed(tmp_path: Path) -> None:
    _, store, provider, _, job_id = await _exhausted_job(tmp_path)
    authorization, _ = await _authorize(store, job_id)
    path = _authorization_path(tmp_path, job_id)
    candidate = authorization.model_copy(update={"job_id": uuid4(), "fingerprint": "0" * 64})
    wrong_job = SpeechRetryBudgetAuthorization.model_validate(
        candidate.model_copy(
            update={"fingerprint": speech_retry_budget_fingerprint(candidate)}
        ).model_dump(mode="python")
    )
    path.write_text(
        json.dumps(wrong_job.model_dump(mode="json")),
        encoding="utf-8",
    )

    with pytest.raises(SpeechRetryBudgetAuthorizationError, match="drifted"):
        await store.effective_limits(
            job_id=job_id,
            target_stage_attempt=authorization.target_stage_attempt,
            base_maximum_requests_per_job=BASE_REQUESTS,
            base_maximum_tts_job_cost_usd=BASE_COST,
            estimated_cost_per_request_usd=PER_REQUEST,
        )
    await provider.close()


@pytest.mark.asyncio
async def test_pinned_request_count_and_cost_drift_fail_closed(tmp_path: Path) -> None:
    remote_store, store, provider, _, job_id = await _exhausted_job(tmp_path)
    authorization, _ = await _authorize(store, job_id)
    records = await remote_store.list_records()
    record = records[0]
    path = (
        tmp_path
        / "production"
        / str(job_id)
        / "generating_narration"
        / f"attempt-{record.attempt_number}"
        / "remote-speech-jobs"
        / f"{record.segment_id}.json"
    )
    drifted = record.model_copy(
        update={"reported_cost": SpeechReportedCost(currency="USD", amount=Decimal("0.002"))}
    )
    path.write_bytes(serialize_remote_speech_job(drifted))

    with pytest.raises(SpeechRetryBudgetAuthorizationError, match="source records drifted"):
        await store.effective_limits(
            job_id=job_id,
            target_stage_attempt=authorization.target_stage_attempt,
            base_maximum_requests_per_job=BASE_REQUESTS,
            base_maximum_tts_job_cost_usd=BASE_COST,
            estimated_cost_per_request_usd=PER_REQUEST,
        )
    await provider.close()


def test_authorization_rejects_stale_count_and_forged_fingerprint() -> None:
    values = _authorization_values()
    with pytest.raises(ValueError, match="request count"):
        SpeechRetryBudgetAuthorization.create(
            **{**values, "current_durable_request_count": 5}
        )
    authorization = SpeechRetryBudgetAuthorization.create(**values)
    with pytest.raises(ValueError, match="fingerprint differs"):
        SpeechRetryBudgetAuthorization.model_validate(
            authorization.model_copy(update={"fingerprint": "f" * 64}).model_dump(
                mode="python"
            )
        )


async def _exhausted_job(
    root: Path,
) -> tuple[
    LocalRemoteSpeechJobStore,
    FilesystemSpeechRetryBudgetAuthorizationStore,
    OpenRouterSpeechGenerationProvider,
    list[int],
    UUID,
]:
    remote_store = LocalRemoteSpeechJobStore(root)
    retry_store = FilesystemSpeechRetryBudgetAuthorizationStore(root)
    calls = [0]

    def success(_: httpx.Request) -> httpx.Response:
        calls[0] += 1
        return httpx.Response(
            200,
            content=b"\x01\x00" * 24_000,
            headers={"content-type": "application/octet-stream"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(success))
    provider = OpenRouterSpeechGenerationProvider(
        api_key="fake-key-never-real",
        model="hexgrad/kokoro-82m",
        voice="configured-spanish-voice",
        estimated_cost_usd=PER_REQUEST,
        maximum_authorized_cost_usd=BASE_COST,
        allow_billable_requests=True,
        remote_job_store=remote_store,
        retry_budget_store=retry_store,
        maximum_requests_per_job=BASE_REQUESTS,
        client=client,
    )
    job_id = _request(attempt=1, replacement=False).job_id
    for ordinal in range(1, 7):
        await provider.generate(_job_request(job_id, attempt=ordinal, ordinal=ordinal))
    calls[0] = 0
    manifest = _initial_manifest().model_copy(update={"attempt_number": 6})
    target = root / "production" / str(job_id) / "generating_narration" / "attempt-6"
    target.mkdir(parents=True, exist_ok=True)
    (target / "speech-generation-manifest.json").write_bytes(
        serialize_speech_manifest(manifest)
    )
    return remote_store, retry_store, provider, calls, job_id


def _job_request(job_id: UUID, *, attempt: int, ordinal: int):
    request = _request(attempt=attempt, replacement=False)
    return request.model_copy(
        update={
            "job_id": job_id,
            "segment": request.segment.model_copy(
                update={
                    "segment_id": f"segment-{ordinal:032x}",
                    "normalized_text_hash": f"{ordinal:064x}",
                }
            ),
        }
    )


async def _authorize(
    store: FilesystemSpeechRetryBudgetAuthorizationStore,
    job_id: UUID,
    *,
    new_requests: int = 7,
    new_cost: Decimal = Decimal("0.007"),
    additional_requests: int = 1,
    additional_cost: Decimal = PER_REQUEST,
    operator_id: str = "operator-test",
):
    return await store.authorize(
        job_id=job_id,
        job_status=ProductionJobStatus.FAILED,
        current_stage=ProductionStage.GENERATING_NARRATION,
        error_code="speech_generation_invalid",
        source_stage_attempt=6,
        target_stage_attempt=7,
        base_maximum_requests_per_job=BASE_REQUESTS,
        base_maximum_tts_job_cost_usd=BASE_COST,
        new_maximum_requests_per_job=new_requests,
        new_maximum_tts_job_cost_usd=new_cost,
        maximum_additional_provider_requests=additional_requests,
        maximum_additional_estimated_cost_usd=additional_cost,
        estimated_cost_per_request_usd=PER_REQUEST,
        operator_id=operator_id,
        clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
    )


def _speech_cost(root: Path, job_id: UUID):
    return derive_durable_job_cost_summary(
        job_id=job_id,
        job_root=root / "production" / str(job_id),
    ).category(JobCostCategory.SPEECH)


def _authorization_path(root: Path, job_id: UUID) -> Path:
    return (
        root
        / "production"
        / str(job_id)
        / "generating_narration"
        / "attempt-7"
        / "speech-retry-budget-authorization.json"
    )


def _remote_record_path(root: Path, job_id: UUID, record) -> Path:
    return (
        root
        / "production"
        / str(job_id)
        / "generating_narration"
        / f"attempt-{record.attempt_number}"
        / "remote-speech-jobs"
        / f"{record.segment_id}.json"
    )


def _authorization_values() -> dict[str, object]:
    from backend.src.production.speech_generation.retry_budget import (
        SpeechRetryBudgetSourceRecord,
    )

    records = tuple(
        SpeechRetryBudgetSourceRecord(
            attempt_number=index,
            segment_id=f"segment-{index:032x}",
            request_fingerprint=f"{index:064x}",
            durable_sha256=f"{index + 10:064x}",
        )
        for index in range(1, 7)
    )
    from backend.src.production.speech_generation.retry_budget import (
        _source_records_fingerprint,
    )

    return {
        "job_id": uuid4(),
        "source_stage_attempt": 6,
        "target_stage_attempt": 7,
        "source_manifest_sha256": "a" * 64,
        "source_remote_records": records,
        "source_remote_records_fingerprint": _source_records_fingerprint(records),
        "current_durable_request_count": 6,
        "current_accounted_tts_cost_usd": BASE_COST,
        "base_maximum_requests_per_job": BASE_REQUESTS,
        "base_maximum_tts_job_cost_usd": BASE_COST,
        "new_maximum_requests_per_job": 7,
        "new_maximum_tts_job_cost_usd": Decimal("0.007"),
        "maximum_additional_provider_requests": 1,
        "maximum_additional_estimated_cost_usd": PER_REQUEST,
        "estimated_cost_per_request_usd": PER_REQUEST,
        "operator_id": "operator-test",
        "authorized_at": datetime(2026, 8, 13, tzinfo=UTC),
    }
