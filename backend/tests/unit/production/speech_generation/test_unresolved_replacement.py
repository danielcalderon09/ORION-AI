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
from backend.src.production.speech_generation.remote_models import RemoteSpeechJobStatus
from backend.src.production.speech_generation.serialization import serialize_speech_manifest
from backend.src.production.speech_generation.uncertainty_resolution import (
    SpeechSubmissionResolution,
    SpeechSubmissionResolutionProvenance,
    SpeechSubmissionResolutionStatus,
    SpeechUncertaintyResolver,
)
from backend.src.production.speech_generation.unresolved_replacement import (
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
async def test_unresolved_without_authorization_stays_blocked(tmp_path: Path) -> None:
    record, _, remote_store = await _unresolved_workspace(tmp_path)
    provider = _provider(tmp_path, remote_store, lambda _: pytest.fail("provider called"))
    with pytest.raises(SpeechProviderUncertainError):
        await provider.generate(_request(attempt=2))
    assert (await remote_store.list_records()) == (record,)
    await provider.close()


@pytest.mark.asyncio
async def test_authorization_requires_duplicate_risk_acknowledgement(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate-charge risk"):
        await _authorization(tmp_path, acknowledge_duplicate_charge_risk=False)


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
):
    return OpenRouterSpeechGenerationProvider(
        api_key="fake-key-never-real",
        model="hexgrad/kokoro-82m",
        voice="configured-spanish-voice",
        estimated_cost_usd=Decimal("0.0001"),
        maximum_authorized_cost_usd=Decimal("0.01"),
        allow_billable_requests=True,
        remote_job_store=remote_store,
        unresolved_replacement_store=replacement_store,
        maximum_requests_per_job=20,
        max_audio_bytes=200_000,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        owns_client=True,
    )
