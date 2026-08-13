"""Offline one-shot authorization tests for unresolved TTS replacement."""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from backend.src.production.cost_accounting.durable_reader import (
    derive_durable_job_cost_summary,
)
from backend.src.production.cost_accounting.models import JobCostCategory
from backend.src.production.speech_generation.exceptions import (
    SpeechProviderUncertainError,
    SpeechReplacementLineageError,
    SpeechUncertaintyResolutionError,
)
from backend.src.production.speech_generation.models import (
    SpeechGenerationManifestStatus,
    SpeechSegmentStatus,
    summarize_speech_entries,
)
from backend.src.production.speech_generation.providers.openrouter_provider import (
    OpenRouterSpeechGenerationProvider,
)
from backend.src.production.speech_generation.remote_job_store import (
    LocalRemoteSpeechJobStore,
)
from backend.src.production.speech_generation.remote_models import (
    RemoteSpeechJobRecord,
    RemoteSpeechJobStatus,
)
from backend.src.production.speech_generation.remote_serialization import (
    serialize_remote_speech_job,
)
from backend.src.production.speech_generation.serialization import serialize_speech_manifest
from backend.src.production.speech_generation.uncertainty_resolution import (
    SpeechSubmissionResolution,
    SpeechSubmissionResolutionProvenance,
    SpeechSubmissionResolutionStatus,
    SpeechUncertaintyResolver,
)
from backend.src.production.speech_generation.unresolved_replacement import (
    SpeechUnresolvedReplacementAuthorization,
    SpeechUnresolvedReplacementPermit,
    SpeechUnresolvedReplacementStore,
)
from backend.tests.unit.production.speech_generation.conftest import (
    NOW,
    source_script,
    speech_configuration,
    speech_requests,
)
from backend.tests.unit.production.speech_generation.remote_test_helpers import (
    SEGMENT_ID,
    remote_record_for_status,
)
from backend.tests.unit.production.speech_generation.test_storage_and_manifest import (
    _initial_manifest,
)


async def _unresolved_workspace(
    root: Path,
    *,
    resolution_status: SpeechSubmissionResolutionStatus = (
        SpeechSubmissionResolutionStatus.UNRESOLVED
    ),
):
    record = remote_record_for_status(RemoteSpeechJobStatus.UNCERTAIN)
    remote_store = LocalRemoteSpeechJobStore(root)
    await remote_store.create(record)
    manifest = _initial_manifest()
    entry = manifest.entries[0].model_copy(
        update={
            "segment_id": SEGMENT_ID,
            "source_scene_id": "scene-001",
            "status": SpeechSegmentStatus.UNCERTAIN,
            "error_code": "remote_submission_uncertain",
        }
    )
    entries = (entry,)
    manifest = manifest.model_copy(
        update={
            "entries": entries,
            "summary": summarize_speech_entries(entries),
            "status": SpeechGenerationManifestStatus.UNCERTAIN,
        }
    )
    attempt = root / "production" / str(record.job_id) / "generating_narration" / "attempt-1"
    attempt.mkdir(parents=True, exist_ok=True)
    (attempt / "speech-generation-manifest.json").write_bytes(
        serialize_speech_manifest(manifest)
    )
    resolution = SpeechSubmissionResolution.create(
        job_id=record.job_id,
        attempt_number=1,
        segment_id=SEGMENT_ID,
        scene_id="scene-001",
        request_fingerprint=record.request_fingerprint,
        resolution=resolution_status,
        provenance=SpeechSubmissionResolutionProvenance.OPERATOR_ASSERTED,
        resolved_at=NOW + timedelta(minutes=1),
        operator_id="operator-1",
        evidence_reference="offline-audit-1",
        acknowledge_new_submission=(
            resolution_status is SpeechSubmissionResolutionStatus.CONFIRMED_NOT_SUBMITTED
        ),
    )
    await SpeechUncertaintyResolver(root).resolve(resolution)
    return record, resolution, remote_store


async def _authorization(root: Path, **updates: object):
    record, resolution, remote_store = await _unresolved_workspace(root)
    values: dict[str, object] = {
        "job_id": record.job_id,
        "source_attempt_number": 1,
        "scene_id": "scene-001",
        "segment_id": SEGMENT_ID,
        "request_fingerprint": record.request_fingerprint,
        "resolution_fingerprint": resolution.fingerprint,
        "maximum_additional_provider_requests": 1,
        "maximum_additional_estimated_cost_usd": Decimal("0.001"),
        "authorized_at": NOW + timedelta(minutes=2),
        "operator_id": "operator-1",
        "acknowledge_duplicate_charge_risk": True,
    }
    values.update(updates)
    store = SpeechUnresolvedReplacementStore(root)
    authorization = await store.authorize(**values)
    return record, resolution, remote_store, store, authorization


@pytest.mark.asyncio
@pytest.mark.parametrize("stage_attempt", (2, 5))
async def test_unresolved_without_authorization_stays_blocked(
    tmp_path: Path, stage_attempt: int
) -> None:
    record, _, remote_store = await _unresolved_workspace(tmp_path)
    provider = _provider(tmp_path, remote_store, lambda _: pytest.fail("provider called"))
    with pytest.raises(SpeechReplacementLineageError):
        await provider.generate(_request(attempt=stage_attempt))
    assert (await remote_store.list_records()) == (record,)
    await provider.close()


@pytest.mark.asyncio
async def test_authorization_requires_duplicate_risk_acknowledgement(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate-charge risk"):
        await _authorization(tmp_path, acknowledge_duplicate_charge_risk=False)


@pytest.mark.asyncio
async def test_authorization_target_must_be_source_plus_one(tmp_path: Path) -> None:
    _, _, _, _, authorization = await _authorization(tmp_path)
    with pytest.raises(ValueError, match="target the next remote submission attempt"):
        SpeechUnresolvedReplacementAuthorization.create(
            **authorization.model_dump(
                mode="python",
                exclude={"fingerprint", "target_attempt_number"},
            ),
            target_attempt_number=3,
        )


@pytest.mark.asyncio
async def test_authorization_is_one_shot_and_creation_has_no_provider_call(tmp_path: Path) -> None:
    record, resolution, _, store, authorization = await _authorization(tmp_path)
    duplicate = await store.authorize(
        job_id=record.job_id,
        source_attempt_number=1,
        scene_id="scene-001",
        segment_id=SEGMENT_ID,
        request_fingerprint=record.request_fingerprint,
        resolution_fingerprint=resolution.fingerprint,
        maximum_additional_provider_requests=1,
        maximum_additional_estimated_cost_usd=Decimal("0.001"),
        authorized_at=NOW + timedelta(minutes=9),
        operator_id="operator-1",
        acknowledge_duplicate_charge_risk=True,
    )
    assert duplicate == authorization
    assert authorization.maximum_additional_provider_requests == 1
    assert authorization.maximum_additional_estimated_cost_usd == Decimal("0.001")
    permit = await store.permit(
        job_id=authorization.job_id,
        target_attempt_number=2,
        segment_id=SEGMENT_ID,
        estimated_cost_usd=Decimal("0.001"),
    )
    assert permit is not None
    await store.consume(
        permit=permit,
        estimated_cost_usd=Decimal("0.001"),
        consumed_at=NOW + timedelta(minutes=3),
    )
    with pytest.raises(SpeechUncertaintyResolutionError, match="exhausted"):
        await store.permit(
            job_id=authorization.job_id,
            target_attempt_number=2,
            segment_id=SEGMENT_ID,
            estimated_cost_usd=Decimal("0.001"),
        )


@pytest.mark.asyncio
async def test_forged_replacement_identity_is_rejected(tmp_path: Path) -> None:
    _, _, _, _, authorization = await _authorization(tmp_path)
    with pytest.raises(ValueError, match="submission identity differs"):
        SpeechUnresolvedReplacementPermit(
            authorization=authorization,
            replacement_submission_identity="f" * 64,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("scene_id", "scene-999", "identity drifted"),
        ("segment_id", "segment-" + "9" * 32, "source artifact"),
        ("request_fingerprint", "f" * 64, "fingerprint drifted"),
        ("resolution_fingerprint", "e" * 64, "resolution fingerprint drifted"),
    ],
)
async def test_authorization_pins_source(
    tmp_path: Path, field: str, value: str, match: str
) -> None:
    with pytest.raises(SpeechUncertaintyResolutionError, match=match):
        await _authorization(tmp_path, **{field: value})


@pytest.mark.asyncio
async def test_authorization_rejects_wrong_job(tmp_path: Path) -> None:
    with pytest.raises(SpeechUncertaintyResolutionError, match="source artifact"):
        await _authorization(tmp_path, job_id=uuid4())


@pytest.mark.asyncio
async def test_confirmed_not_submitted_uses_existing_mechanism(tmp_path: Path) -> None:
    record, resolution, _ = await _unresolved_workspace(
        tmp_path,
        resolution_status=SpeechSubmissionResolutionStatus.CONFIRMED_NOT_SUBMITTED,
    )
    with pytest.raises(SpeechUncertaintyResolutionError, match="requires an unresolved"):
        await SpeechUnresolvedReplacementStore(tmp_path).authorize(
            job_id=record.job_id,
            source_attempt_number=1,
            scene_id="scene-001",
            segment_id=SEGMENT_ID,
            request_fingerprint=record.request_fingerprint,
            resolution_fingerprint=resolution.fingerprint,
            maximum_additional_provider_requests=1,
            maximum_additional_estimated_cost_usd=Decimal("0.001"),
            authorized_at=NOW + timedelta(minutes=2),
            operator_id="operator-1",
            acknowledge_duplicate_charge_risk=True,
        )


@pytest.mark.asyncio
async def test_replacement_posts_once_and_costs_are_independent(tmp_path: Path) -> None:
    record, _, remote_store, replacement_store, _ = await _authorization(tmp_path)
    calls = 0

    def success(_):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            content=b"\x01\x00" * 4_800,
            headers={"content-type": "application/octet-stream"},
        )

    provider = _provider(tmp_path, remote_store, success, replacement_store=replacement_store)
    await provider.generate(_request(attempt=2))
    with pytest.raises(SpeechProviderUncertainError):
        await provider.generate(_request(attempt=2))
    await provider.generate(_request(attempt=2, replacement=False))
    assert calls == 2
    records = await remote_store.list_records()
    assert len(records) == 3
    assert records[0] == record
    assert records[1].status is RemoteSpeechJobStatus.COMPLETED
    assert records[1].metadata["replacement_submission_identity"]
    summary = derive_durable_job_cost_summary(
        job_id=record.job_id,
        job_root=tmp_path / "production" / str(record.job_id),
    ).category(JobCostCategory.SPEECH)
    assert summary.request_count == 3
    assert summary.estimated_fallback_request_count == 3
    assert summary.accounted_cost_usd == (
        record.estimated_cost.estimated_maximum_cost + Decimal("0.0002")
    )
    await provider.close()


@pytest.mark.asyncio
async def test_uncertain_replacement_requires_another_authorization(tmp_path: Path) -> None:
    _, _, remote_store, replacement_store, _ = await _authorization(tmp_path)
    calls = 0

    def uncertain(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("offline uncertain replacement", request=request)

    provider = _provider(
        tmp_path,
        remote_store,
        uncertain,
        replacement_store=replacement_store,
    )
    with pytest.raises(SpeechProviderUncertainError):
        await provider.generate(_request(attempt=2))
    with pytest.raises(SpeechProviderUncertainError):
        await provider.generate(_request(attempt=2))
    assert calls == 1
    records = await remote_store.list_records()
    assert records[-1].attempt_number == 2
    assert records[-1].status is RemoteSpeechJobStatus.UNCERTAIN
    await provider.close()


@pytest.mark.asyncio
async def test_chained_unresolved_replacement_uses_immediate_parent_once(
    tmp_path: Path,
) -> None:
    remote_store, replacement_store, first, second = await _two_uncertain_attempts(
        tmp_path
    )
    calls = 0

    def success(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        consumption = (
            tmp_path
            / "production"
            / str(first.job_id)
            / "generating_narration"
            / "attempt-3"
            / "speech-rc"
            / f"{SEGMENT_ID}.json"
        )
        assert consumption.exists(), "authorization must be consumed before POST"
        return httpx.Response(
            200,
            content=b"\x01\x00" * 4_800,
            headers={"content-type": "application/octet-stream"},
        )

    provider = _provider(
        tmp_path,
        remote_store,
        success,
        replacement_store=replacement_store,
        estimated_cost=Decimal("0.001"),
        timeout_seconds=180,
    )
    await provider.generate(_request(attempt=3))
    records = await remote_store.list_records()
    assert calls == 1
    assert [record.status for record in records] == [
        RemoteSpeechJobStatus.UNCERTAIN,
        RemoteSpeechJobStatus.UNCERTAIN,
        RemoteSpeechJobStatus.COMPLETED,
    ]
    assert len({record.request_fingerprint for record in records}) == 3
    assert records[0] == first
    assert records[1] == second
    assert records[2].metadata["replacement_source_attempt"] == 2
    assert records[2].metadata["replacement_submission_identity"]
    assert await replacement_store.uncertainty_is_covered(
        record=first, job_records=records
    )
    assert await replacement_store.uncertainty_is_covered(
        record=second, job_records=records
    )
    summary = derive_durable_job_cost_summary(
        job_id=first.job_id,
        job_root=tmp_path / "production" / str(first.job_id),
    ).category(JobCostCategory.SPEECH)
    assert summary.request_count == 3
    assert summary.accounted_cost_usd == Decimal("0.003")
    assert records[0].estimated_cost.estimated_maximum_cost == Decimal("0.001")
    assert records[1].estimated_cost.estimated_maximum_cost == Decimal("0.001")
    await provider.close()


@pytest.mark.asyncio
async def test_later_stage_attempt_uses_authorized_remote_attempt_three(
    tmp_path: Path,
) -> None:
    remote_store, replacement_store, first, second = await _two_uncertain_attempts(
        tmp_path
    )
    for attempt in (3, 4):
        _write_local_stage_manifest(tmp_path, first, attempt=attempt, failed=True)
    _write_local_stage_manifest(tmp_path, first, attempt=5, failed=False)
    before = derive_durable_job_cost_summary(
        job_id=first.job_id,
        job_root=tmp_path / "production" / str(first.job_id),
    ).category(JobCostCategory.SPEECH)
    calls = 0

    def success(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        consumption = (
            tmp_path
            / "production"
            / str(first.job_id)
            / "generating_narration"
            / "attempt-3"
            / "speech-rc"
            / f"{SEGMENT_ID}.json"
        )
        assert consumption.exists(), "authorization must be consumed before POST"
        return httpx.Response(
            200,
            content=b"\x01\x00" * 4_800,
            headers={"content-type": "application/octet-stream"},
        )

    provider = _provider(
        tmp_path,
        remote_store,
        success,
        replacement_store=replacement_store,
        estimated_cost=Decimal("0.001"),
        timeout_seconds=180,
    )
    await provider.generate(_request(attempt=5))
    records = await remote_store.list_records()
    after = derive_durable_job_cost_summary(
        job_id=first.job_id,
        job_root=tmp_path / "production" / str(first.job_id),
    ).category(JobCostCategory.SPEECH)

    assert before.request_count == 2
    assert before.accounted_cost_usd == Decimal("0.002")
    assert calls == 1
    assert [record.attempt_number for record in records] == [1, 2, 3]
    assert all(record.attempt_number != 4 for record in records)
    assert records[0] == first
    assert records[1] == second
    assert records[2].status is RemoteSpeechJobStatus.COMPLETED
    assert records[2].metadata["originating_stage_attempt"] == 5
    assert records[2].metadata["replacement_source_attempt"] == 2
    assert len({record.request_fingerprint for record in records}) == 3
    assert after.request_count == 3
    assert after.accounted_cost_usd == Decimal("0.003")
    assert all(
        (
            tmp_path
            / "production"
            / str(first.job_id)
            / "generating_narration"
            / f"attempt-{attempt}"
            / "speech-generation-manifest.json"
        ).exists()
        for attempt in range(1, 6)
    )
    with pytest.raises(SpeechUncertaintyResolutionError, match="exhausted"):
        await replacement_store.permit(
            job_id=first.job_id,
            target_attempt_number=3,
            segment_id=SEGMENT_ID,
            estimated_cost_usd=Decimal("0.001"),
        )
    await provider.close()


@pytest.mark.asyncio
async def test_later_stage_timeout_persists_remote_three_diagnostic_once(
    tmp_path: Path,
) -> None:
    remote_store, replacement_store, first, _ = await _two_uncertain_attempts(tmp_path)
    for attempt in (3, 4):
        _write_local_stage_manifest(tmp_path, first, attempt=attempt, failed=True)
    _write_local_stage_manifest(tmp_path, first, attempt=5, failed=False)
    calls = 0

    def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("offline stage-5 remote-3 timeout", request=request)

    provider = _provider(
        tmp_path,
        remote_store,
        timeout,
        replacement_store=replacement_store,
        estimated_cost=Decimal("0.001"),
        timeout_seconds=180,
    )
    with pytest.raises(SpeechProviderUncertainError):
        await provider.generate(_request(attempt=5))
    records = await remote_store.list_records()
    remote_three = records[-1]
    summary = derive_durable_job_cost_summary(
        job_id=first.job_id,
        job_root=tmp_path / "production" / str(first.job_id),
    ).category(JobCostCategory.SPEECH)

    assert calls == 1
    assert [record.attempt_number for record in records] == [1, 2, 3]
    assert remote_three.status is RemoteSpeechJobStatus.UNCERTAIN
    assert remote_three.safe_error_code == "speech_transport_timeout"
    assert remote_three.transport_diagnostic is not None
    assert remote_three.transport_diagnostic.timeout_seconds == Decimal("180")
    assert remote_three.transport_diagnostic.elapsed_seconds is not None
    assert summary.request_count == 3
    assert summary.accounted_cost_usd == Decimal("0.003")
    with pytest.raises(SpeechReplacementLineageError):
        await provider.generate(_request(attempt=5))
    assert calls == 1
    assert all(record.attempt_number != 4 for record in records)
    await provider.close()


@pytest.mark.asyncio
async def test_chained_third_uncertainty_is_accounted_once_and_stops(
    tmp_path: Path,
) -> None:
    remote_store, replacement_store, first, second = await _two_uncertain_attempts(
        tmp_path
    )
    calls = 0

    def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("offline attempt-3 timeout", request=request)

    provider = _provider(
        tmp_path,
        remote_store,
        timeout,
        replacement_store=replacement_store,
        estimated_cost=Decimal("0.001"),
        timeout_seconds=180,
    )
    with pytest.raises(SpeechProviderUncertainError):
        await provider.generate(_request(attempt=3))
    records = await remote_store.list_records()
    assert calls == 1
    assert len(records) == 3
    assert records[0] == first
    assert records[1] == second
    assert records[2].status is RemoteSpeechJobStatus.UNCERTAIN
    assert records[2].transport_diagnostic is not None
    assert records[2].transport_diagnostic.timeout_seconds == Decimal("180")
    assert len({record.request_fingerprint for record in records}) == 3
    summary = derive_durable_job_cost_summary(
        job_id=first.job_id,
        job_root=tmp_path / "production" / str(first.job_id),
    ).category(JobCostCategory.SPEECH)
    assert summary.request_count == 3
    assert summary.accounted_cost_usd == Decimal("0.003")
    with pytest.raises(SpeechProviderUncertainError):
        await provider.generate(_request(attempt=3))
    assert calls == 1
    assert not (
        tmp_path
        / "production"
        / str(first.job_id)
        / "generating_narration"
        / "attempt-4"
    ).exists()
    await provider.close()


@pytest.mark.asyncio
async def test_chain_rejects_non_immediate_parent_authorization(tmp_path: Path) -> None:
    remote_store, replacement_store, first, second = await _two_uncertain_attempts(
        tmp_path
    )
    authorization = (
        tmp_path
        / "production"
        / str(first.job_id)
        / "generating_narration"
        / "attempt-3"
        / "speech-ra"
        / f"{SEGMENT_ID}.json"
    )
    source_authorization = (
        tmp_path
        / "production"
        / str(first.job_id)
        / "generating_narration"
        / "attempt-2"
        / "speech-ra"
        / f"{SEGMENT_ID}.json"
    )
    authorization.write_bytes(source_authorization.read_bytes())
    provider = _provider(
        tmp_path,
        remote_store,
        lambda _: pytest.fail("provider called"),
        replacement_store=replacement_store,
        estimated_cost=Decimal("0.001"),
    )
    with pytest.raises(SpeechReplacementLineageError):
        await provider.generate(_request(attempt=3))
    assert await remote_store.list_records() == (first, second)
    await provider.close()


@pytest.mark.asyncio
async def test_later_stage_replacement_rejects_ambiguous_uncovered_lineage(
    tmp_path: Path,
) -> None:
    remote_store, replacement_store, first, second = await _two_uncertain_attempts(
        tmp_path
    )
    provider = _provider(
        tmp_path,
        remote_store,
        lambda _: pytest.fail("provider called"),
        replacement_store=replacement_store,
        estimated_cost=Decimal("0.001"),
    )
    extra_prepared = provider._prepared_record(_request(attempt=1, replacement=False))
    extra_uncertain = extra_prepared.model_copy(
        update={
            "status": RemoteSpeechJobStatus.UNCERTAIN,
            "submission_started_at": extra_prepared.prepared_at,
            "fresh_submission_permitted": False,
        }
    )
    await remote_store.create(extra_uncertain)

    with pytest.raises(SpeechReplacementLineageError, match="unique eligible parent"):
        await provider.generate(_request(attempt=5))
    assert await remote_store.list_records() == (first, extra_uncertain, second)
    await provider.close()


@pytest.mark.asyncio
async def test_later_stage_replacement_rejects_existing_remote_child(
    tmp_path: Path,
) -> None:
    remote_store, replacement_store, first, second = await _two_uncertain_attempts(
        tmp_path
    )
    provider = _provider(
        tmp_path,
        remote_store,
        lambda _: pytest.fail("provider called"),
        replacement_store=replacement_store,
        estimated_cost=Decimal("0.001"),
    )
    conflicting_child = provider._prepared_record(
        _request(attempt=3), remote_attempt_number=3
    )
    await remote_store.create(conflicting_child)

    with pytest.raises(SpeechReplacementLineageError, match="child already exists"):
        await provider.generate(_request(attempt=5))
    assert await remote_store.list_records() == (first, second, conflicting_child)
    await provider.close()


@pytest.mark.asyncio
async def test_later_stage_replacement_rejects_source_fingerprint_drift(
    tmp_path: Path,
) -> None:
    original, _, remote_store, replacement_store, _ = await _authorization(tmp_path)
    provider = _provider(
        tmp_path,
        remote_store,
        lambda _: pytest.fail("provider called"),
        replacement_store=replacement_store,
    )
    alternate_prepared = provider._prepared_record(_request(attempt=1))
    alternate = alternate_prepared.model_copy(
        update={
            "status": RemoteSpeechJobStatus.UNCERTAIN,
            "submission_started_at": alternate_prepared.prepared_at,
            "fresh_submission_permitted": False,
        }
    )
    source_path = (
        tmp_path
        / "production"
        / str(original.job_id)
        / "generating_narration"
        / "attempt-1"
        / "remote-speech-jobs"
        / f"{SEGMENT_ID}.json"
    )
    source_path.write_bytes(serialize_remote_speech_job(alternate))

    with pytest.raises(SpeechReplacementLineageError, match="authorization is invalid"):
        await provider.generate(_request(attempt=5))
    assert len(await remote_store.list_records()) == 1
    await provider.close()


@pytest.mark.asyncio
async def test_chain_rejects_regression_and_consumed_ancestral_reuse(
    tmp_path: Path,
) -> None:
    remote_store, replacement_store, first, _ = await _two_uncertain_attempts(tmp_path)
    with pytest.raises(SpeechUncertaintyResolutionError, match="exhausted"):
        await replacement_store.permit(
            job_id=first.job_id,
            target_attempt_number=2,
            segment_id=SEGMENT_ID,
            estimated_cost_usd=Decimal("0.001"),
        )
    provider = _provider(
        tmp_path,
        remote_store,
        lambda _: pytest.fail("provider called"),
        replacement_store=replacement_store,
        estimated_cost=Decimal("0.001"),
    )
    with pytest.raises(SpeechProviderUncertainError):
        await provider.generate(_request(attempt=2))
    await provider.close()


@pytest.mark.asyncio
async def test_authorization_rejects_conflicting_sibling(tmp_path: Path) -> None:
    record, resolution, _, store, _ = await _authorization(tmp_path)
    with pytest.raises(SpeechUncertaintyResolutionError, match="conflicting"):
        await store.authorize(
            job_id=record.job_id,
            source_attempt_number=1,
            scene_id="scene-001",
            segment_id=SEGMENT_ID,
            request_fingerprint=record.request_fingerprint,
            resolution_fingerprint=resolution.fingerprint,
            maximum_additional_provider_requests=1,
            maximum_additional_estimated_cost_usd=Decimal("0.002"),
            authorized_at=NOW + timedelta(minutes=3),
            operator_id="operator-2",
            acknowledge_duplicate_charge_risk=True,
        )


@pytest.mark.asyncio
async def test_authorization_rejects_missing_source_resolution(tmp_path: Path) -> None:
    record = remote_record_for_status(RemoteSpeechJobStatus.UNCERTAIN)
    remote_store = LocalRemoteSpeechJobStore(tmp_path)
    await remote_store.create(record)
    with pytest.raises(SpeechUncertaintyResolutionError, match="source artifact"):
        await SpeechUnresolvedReplacementStore(tmp_path).authorize(
            job_id=record.job_id,
            source_attempt_number=1,
            scene_id="scene-001",
            segment_id=SEGMENT_ID,
            request_fingerprint=record.request_fingerprint,
            resolution_fingerprint="f" * 64,
            maximum_additional_provider_requests=1,
            maximum_additional_estimated_cost_usd=Decimal("0.001"),
            authorized_at=NOW + timedelta(minutes=2),
            operator_id="operator-1",
            acknowledge_duplicate_charge_risk=True,
        )


async def _two_uncertain_attempts(
    root: Path,
) -> tuple[
    LocalRemoteSpeechJobStore,
    SpeechUnresolvedReplacementStore,
    RemoteSpeechJobRecord,
    RemoteSpeechJobRecord,
]:
    remote_store = LocalRemoteSpeechJobStore(root)
    replacement_store = SpeechUnresolvedReplacementStore(root)

    async def submit_uncertain(attempt: int) -> RemoteSpeechJobRecord:
        def timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout(f"offline attempt-{attempt}", request=request)

        provider = _provider(
            root,
            remote_store,
            timeout,
            replacement_store=replacement_store,
            estimated_cost=Decimal("0.001"),
            timeout_seconds=180,
        )
        with pytest.raises(SpeechProviderUncertainError):
            await provider.generate(_request(attempt=attempt))
        await provider.close()
        return (await remote_store.list_records())[-1]

    first = await submit_uncertain(1)
    await _resolve_and_authorize(root, first)
    second = await submit_uncertain(2)
    await _resolve_and_authorize(root, second)
    return remote_store, replacement_store, first, second


async def _resolve_and_authorize(root: Path, record: RemoteSpeechJobRecord) -> None:
    assert record.submission_started_at is not None
    _write_uncertain_manifest(root, record)
    resolution = SpeechSubmissionResolution.create(
        job_id=record.job_id,
        attempt_number=record.attempt_number,
        segment_id=record.segment_id,
        scene_id="scene-001",
        request_fingerprint=record.request_fingerprint,
        resolution=SpeechSubmissionResolutionStatus.UNRESOLVED,
        provenance=SpeechSubmissionResolutionProvenance.OPERATOR_ASSERTED,
        resolved_at=record.submission_started_at + timedelta(minutes=1),
        operator_id=f"operator-{record.attempt_number}",
        evidence_reference=f"offline-audit-{record.attempt_number}",
    )
    await SpeechUncertaintyResolver(root).resolve(resolution)
    await SpeechUnresolvedReplacementStore(root).authorize(
        job_id=record.job_id,
        source_attempt_number=record.attempt_number,
        scene_id="scene-001",
        segment_id=record.segment_id,
        request_fingerprint=record.request_fingerprint,
        resolution_fingerprint=resolution.fingerprint,
        maximum_additional_provider_requests=1,
        maximum_additional_estimated_cost_usd=Decimal("0.001"),
        authorized_at=record.submission_started_at + timedelta(minutes=2),
        operator_id=f"operator-{record.attempt_number}",
        acknowledge_duplicate_charge_risk=True,
    )


def _write_uncertain_manifest(root: Path, record: RemoteSpeechJobRecord) -> None:
    manifest = _initial_manifest().model_copy(
        update={"attempt_number": record.attempt_number}
    )
    entry = manifest.entries[0].model_copy(
        update={
            "segment_id": record.segment_id,
            "source_scene_id": "scene-001",
            "status": SpeechSegmentStatus.UNCERTAIN,
            "error_code": "speech_submission_uncertain",
        }
    )
    entries = (entry,)
    manifest = manifest.model_copy(
        update={
            "entries": entries,
            "summary": summarize_speech_entries(entries),
            "status": SpeechGenerationManifestStatus.UNCERTAIN,
        }
    )
    attempt = (
        root
        / "production"
        / str(record.job_id)
        / "generating_narration"
        / f"attempt-{record.attempt_number}"
    )
    attempt.mkdir(parents=True, exist_ok=True)
    (attempt / "speech-generation-manifest.json").write_bytes(
        serialize_speech_manifest(manifest)
    )


def _write_local_stage_manifest(
    root: Path,
    record: RemoteSpeechJobRecord,
    *,
    attempt: int,
    failed: bool,
) -> None:
    manifest = _initial_manifest().model_copy(update={"attempt_number": attempt})
    if failed:
        entry = manifest.entries[0].model_copy(
            update={
                "segment_id": record.segment_id,
                "source_scene_id": "scene-001",
                "status": SpeechSegmentStatus.FAILED,
                "error_code": "speech_replacement_lineage_blocked",
            }
        )
        entries = (entry,)
        manifest = manifest.model_copy(
            update={
                "entries": entries,
                "summary": summarize_speech_entries(entries),
                "status": SpeechGenerationManifestStatus.FAILED,
            }
        )
    target = (
        root
        / "production"
        / str(record.job_id)
        / "generating_narration"
        / f"attempt-{attempt}"
    )
    target.mkdir(parents=True, exist_ok=True)
    (target / "speech-generation-manifest.json").write_bytes(
        serialize_speech_manifest(manifest)
    )


def _request(*, attempt: int, replacement: bool = True):
    configuration = speech_configuration(
        provider="openrouter",
        voice="configured-spanish-voice",
        min_duration_ms=100,
    )
    request = speech_requests(
        source_script(), configuration, index=0 if replacement else 1
    )[0]
    if not replacement:
        return request.model_copy(update={"attempt_number": attempt})
    return request.model_copy(
        update={
            "attempt_number": attempt,
            "segment": request.segment.model_copy(
                update={"segment_id": SEGMENT_ID, "normalized_text_hash": "2" * 64}
            ),
        }
    )


def _provider(
    root: Path,
    remote_store: LocalRemoteSpeechJobStore,
    handler,
    *,
    replacement_store: SpeechUnresolvedReplacementStore | None = None,
    estimated_cost: Decimal = Decimal("0.0001"),
    timeout_seconds: float = 120,
):
    return OpenRouterSpeechGenerationProvider(
        api_key="fake-key-never-real",
        model="hexgrad/kokoro-82m",
        voice="configured-spanish-voice",
        estimated_cost_usd=estimated_cost,
        maximum_authorized_cost_usd=max(Decimal("0.01"), estimated_cost * 20),
        allow_billable_requests=True,
        remote_job_store=remote_store,
        unresolved_replacement_store=replacement_store,
        maximum_requests_per_job=20,
        timeout_seconds=timeout_seconds,
        max_audio_bytes=200_000,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        owns_client=True,
    )
