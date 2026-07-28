import json

import pytest

from backend.src.production.speech_generation.exceptions import (
    SpeechAudioNotFoundError,
)
from backend.src.production.speech_generation.models import (
    ReadSpeechBinaryAsset,
    SpeechBinaryAsset,
    SpeechBinaryAssetMetadata,
)
from backend.src.production.speech_generation.remote_job_store import (
    LocalRemoteSpeechJobStore,
)
from backend.src.production.speech_generation.remote_models import (
    RemoteSpeechJobStatus,
)
from backend.src.production.speech_generation.remote_reconciliation import (
    RemoteSpeechJobReconciler,
    RemoteSpeechReconciliationIssueKind,
    RemoteSpeechRecordExpectation,
)
from backend.src.production.speech_generation.remote_serialization import (
    serialize_remote_speech_job,
)
from backend.tests.unit.production.speech_generation.conftest import NOW
from backend.tests.unit.production.speech_generation.remote_test_helpers import (
    prepared_remote_record,
    remote_record_for_status,
)


class MissingAudioStore:
    async def resolve(self, *, job_id, segment_id):
        del job_id, segment_id
        raise SpeechAudioNotFoundError("missing")

    async def write(self, *, request, content):
        raise AssertionError((request, content))

    async def recover(self, *, request):
        raise AssertionError(request)

    async def read(self, *, asset):
        raise AssertionError(asset)


class MismatchedAudioStore(MissingAudioStore):
    def __init__(self, record) -> None:
        self._record = record

    async def resolve(self, *, job_id, segment_id):
        record = self._record
        assert job_id == record.job_id
        assert segment_id == record.segment_id
        asset = SpeechBinaryAsset(
            asset_id=f"speech-{record.segment_id}",
            segment_id=record.segment_id,
            job_id=record.job_id,
            scene_id="scene-001",
            sequence_index=0,
            sha256="a" * 64,
            size_bytes=44,
            duration_ms=100,
            sample_rate_hz=24_000,
            channel_count=1,
            sample_width_bytes=2,
            frame_count=2_400,
            created_at=NOW,
            storage_path=(
                f"production/{record.job_id}/assets/speech/speech-{record.segment_id}.wav"
            ),
            metadata=SpeechBinaryAssetMetadata(
                source_script_artifact_id=record.source_script_artifact_id,
                source_script_sha256=record.source_script_sha256,
                normalized_text_hash="f" * 64,
                configuration_fingerprint="e" * 64,
                provider=record.provider,
                requested_voice=record.voice,
                requested_language=record.language,
                deterministic=False,
            ),
        )
        return ReadSpeechBinaryAsset(asset=asset, content=b"x")


def _expectation(record, **updates):
    values = {
        "job_id": record.job_id,
        "attempt_number": record.attempt_number,
        "segment_id": record.segment_id,
        "request_fingerprint": record.request_fingerprint,
        "capability_snapshot_hash": record.capability_snapshot_hash,
        "pricing_snapshot_hash": record.pricing_snapshot_hash,
    }
    values.update(updates)
    return RemoteSpeechRecordExpectation(**values)


def _path(root, record):
    return (
        root
        / "production"
        / str(record.job_id)
        / "generating_narration"
        / f"attempt-{record.attempt_number}"
        / "remote-speech-jobs"
        / f"{record.segment_id}.json"
    )


def _reconciler(root):
    return RemoteSpeechJobReconciler(
        workspace_root=root,
        audio_store=MissingAudioStore(),
        max_record_bytes=1_000_000,
    )


@pytest.mark.asyncio
async def test_remote_reconciliation_healthy_state_and_no_mutation(tmp_path) -> None:
    record = prepared_remote_record()
    store = LocalRemoteSpeechJobStore(tmp_path)
    await store.create(record)
    path = _path(tmp_path, record)
    before = path.read_bytes()

    report = await _reconciler(tmp_path).reconcile(expectations=(_expectation(record),))

    assert report.healthy
    assert report.records_checked == 1
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_remote_reconciliation_detects_missing_orphan_and_snapshot_drift(
    tmp_path,
) -> None:
    record = prepared_remote_record()
    store = LocalRemoteSpeechJobStore(tmp_path)
    await store.create(record)
    reconciler = _reconciler(tmp_path)

    orphan = await reconciler.reconcile()
    assert RemoteSpeechReconciliationIssueKind.ORPHAN_RECORD in {
        issue.kind for issue in orphan.issues
    }

    drift = await reconciler.reconcile(
        expectations=(
            _expectation(
                record,
                capability_snapshot_hash="a" * 64,
                pricing_snapshot_hash="b" * 64,
            ),
        )
    )
    assert {
        RemoteSpeechReconciliationIssueKind.CAPABILITY_MISMATCH,
        RemoteSpeechReconciliationIssueKind.PRICING_MISMATCH,
    }.issubset({issue.kind for issue in drift.issues})

    missing_root = tmp_path / "missing"
    missing = await _reconciler(missing_root).reconcile(expectations=(_expectation(record),))
    assert {issue.kind for issue in missing.issues} == {
        RemoteSpeechReconciliationIssueKind.MISSING_RECORD
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "expected_kind"),
    [
        (
            lambda payload: payload.update({"request_fingerprint": "f" * 64}),
            RemoteSpeechReconciliationIssueKind.FINGERPRINT_MISMATCH,
        ),
        (
            lambda payload: payload.update({"authorization": None}),
            RemoteSpeechReconciliationIssueKind.MISSING_AUTHORIZATION,
        ),
        (
            lambda payload: payload["authorization"].update({"maximum_authorized_cost": "0.001"}),
            RemoteSpeechReconciliationIssueKind.ESTIMATE_OVER_AUTHORIZATION,
        ),
    ],
)
async def test_remote_reconciliation_classifies_corrupt_safety_fields(
    tmp_path,
    mutation,
    expected_kind,
) -> None:
    record = prepared_remote_record()
    path = _path(tmp_path, record)
    path.parent.mkdir(parents=True)
    payload = json.loads(serialize_remote_speech_job(record))
    mutation(payload)
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    report = await _reconciler(tmp_path).reconcile(expectations=(_expectation(record),))
    assert expected_kind in {issue.kind for issue in report.issues}


@pytest.mark.asyncio
async def test_remote_reconciliation_detects_uncertain_retry_and_missing_identity(
    tmp_path,
) -> None:
    uncertain = remote_record_for_status(RemoteSpeechJobStatus.UNCERTAIN)
    uncertain_path = _path(tmp_path, uncertain)
    uncertain_path.parent.mkdir(parents=True)
    payload = json.loads(serialize_remote_speech_job(uncertain))
    payload["fresh_submission_permitted"] = True
    uncertain_path.write_text(json.dumps(payload), encoding="utf-8")
    report = await _reconciler(tmp_path).reconcile(expectations=(_expectation(uncertain),))
    assert RemoteSpeechReconciliationIssueKind.UNCERTAIN_RETRY_VIOLATION in {
        issue.kind for issue in report.issues
    }

    submitted = remote_record_for_status(RemoteSpeechJobStatus.SUBMITTED)
    submitted_path = _path(tmp_path, submitted)
    payload = json.loads(serialize_remote_speech_job(submitted))
    payload["remote_job_id"] = None
    submitted_path.write_text(json.dumps(payload), encoding="utf-8")
    report = await _reconciler(tmp_path).reconcile(expectations=(_expectation(submitted),))
    assert RemoteSpeechReconciliationIssueKind.MISSING_REMOTE_IDENTITY in {
        issue.kind for issue in report.issues
    }


@pytest.mark.asyncio
async def test_remote_reconciliation_detects_corruption_and_missing_local_output(
    tmp_path,
) -> None:
    completed = remote_record_for_status(RemoteSpeechJobStatus.COMPLETED)
    store = LocalRemoteSpeechJobStore(tmp_path)
    await store.create(completed)
    report = await _reconciler(tmp_path).reconcile(expectations=(_expectation(completed),))
    assert RemoteSpeechReconciliationIssueKind.MISSING_LOCAL_OUTPUT in {
        issue.kind for issue in report.issues
    }

    path = _path(tmp_path, completed)
    path.write_bytes(b"{not-json")
    report = await _reconciler(tmp_path).reconcile()
    assert RemoteSpeechReconciliationIssueKind.CORRUPT_RECORD in {
        issue.kind for issue in report.issues
    }


@pytest.mark.asyncio
async def test_remote_reconciliation_detects_local_audio_provenance_drift(
    tmp_path,
) -> None:
    record = prepared_remote_record()
    store = LocalRemoteSpeechJobStore(tmp_path)
    await store.create(record)
    reconciler = RemoteSpeechJobReconciler(
        workspace_root=tmp_path,
        audio_store=MismatchedAudioStore(record),
        max_record_bytes=1_000_000,
    )
    report = await reconciler.reconcile(expectations=(_expectation(record),))
    assert RemoteSpeechReconciliationIssueKind.LOCAL_PROVENANCE_MISMATCH in {
        issue.kind for issue in report.issues
    }
