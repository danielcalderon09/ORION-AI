"""Offline tests for safe TTS uncertain-submission resolution."""

from datetime import timedelta
from pathlib import Path

import pytest

from backend.src.production.speech_generation.models import (
    SpeechGenerationManifestStatus,
    SpeechSegmentStatus,
    summarize_speech_entries,
)
from backend.src.production.speech_generation.remote_job_store import (
    LocalRemoteSpeechJobStore,
)
from backend.src.production.speech_generation.remote_models import RemoteSpeechJobStatus
from backend.src.production.speech_generation.remote_recovery import (
    RemoteSpeechRecoveryAction,
    RemoteSpeechRecoveryPolicy,
)
from backend.src.production.speech_generation.serialization import serialize_speech_manifest
from backend.src.production.speech_generation.uncertainty_resolution import (
    SpeechSubmissionResolution,
    SpeechSubmissionResolutionProvenance,
    SpeechSubmissionResolutionStatus,
    SpeechSubmissionResolutionStore,
    SpeechUncertaintyResolver,
    deserialize_speech_submission_resolution,
    serialize_speech_submission_resolution,
)
from backend.tests.unit.production.speech_generation.remote_test_helpers import (
    SEGMENT_ID,
    remote_record_for_status,
)
from backend.tests.unit.production.speech_generation.test_storage_and_manifest import (
    _initial_manifest,
)


def _resolution(record, **updates: object) -> SpeechSubmissionResolution:
    values: dict[str, object] = {
        "job_id": record.job_id,
        "attempt_number": record.attempt_number,
        "segment_id": record.segment_id,
        "scene_id": "scene-001",
        "request_fingerprint": record.request_fingerprint,
        "resolution": SpeechSubmissionResolutionStatus.CONFIRMED_NOT_SUBMITTED,
        "provenance": SpeechSubmissionResolutionProvenance.OPERATOR_ASSERTED,
        "resolved_at": record.prepared_at + timedelta(minutes=1),
        "operator_id": "operator-1",
        "evidence_reference": "ticket-123",
        "acknowledge_new_submission": True,
    }
    values.update(updates)
    return SpeechSubmissionResolution.create(**values)


def test_uncertain_submission_remains_blocked_without_resolution() -> None:
    record = remote_record_for_status(RemoteSpeechJobStatus.UNCERTAIN)
    decision = RemoteSpeechRecoveryPolicy().classify(record=record, verified_local_audio=False)
    assert decision.action is RemoteSpeechRecoveryAction.REQUIRE_MANUAL_REVIEW
    assert decision.safe_to_submit is False
    assert decision.fresh_submission_eligible is False

def test_confirmed_not_submitted_is_only_future_retry_eligibility() -> None:
    record = remote_record_for_status(RemoteSpeechJobStatus.UNCERTAIN)
    resolution = _resolution(record)
    decision = RemoteSpeechRecoveryPolicy().classify(
        record=record,
        verified_local_audio=False,
        resolution=resolution,
    )
    assert decision.action is RemoteSpeechRecoveryAction.PREPARE_FRESH_SUBMISSION
    assert decision.safe_to_submit is False
    assert decision.fresh_submission_eligible is True


def test_resolution_does_not_submit_and_completed_recovery_is_not_retry() -> None:
    record = remote_record_for_status(RemoteSpeechJobStatus.UNCERTAIN)
    resolution = _resolution(
        record,
        resolution=SpeechSubmissionResolutionStatus.CONFIRMED_COMPLETED,
        evidence_reference="provider-reconcile-1",
        acknowledge_new_submission=False,
    )
    decision = RemoteSpeechRecoveryPolicy().classify(
        record=record,
        verified_local_audio=False,
        resolution=resolution,
    )
    assert decision.action is RemoteSpeechRecoveryAction.RECOVER_CONFIRMED_COMPLETED
    assert decision.safe_to_submit is False


def test_resolution_contract_requires_explicit_acknowledgement() -> None:
    record = remote_record_for_status(RemoteSpeechJobStatus.UNCERTAIN)
    with pytest.raises(ValueError, match="retry acknowledgement"):
        _resolution(record, acknowledge_new_submission=False)


@pytest.mark.asyncio
async def test_sidecar_is_idempotent_and_conflicts_fail_closed(tmp_path: Path) -> None:
    record = remote_record_for_status(RemoteSpeechJobStatus.UNCERTAIN)
    store = SpeechSubmissionResolutionStore(tmp_path)
    first = _resolution(record)
    assert await store.create_if_idempotent(first) is True
    assert await store.create_if_idempotent(first) is True
    conflicting = _resolution(record, operator_id="operator-2")
    with pytest.raises(RuntimeError, match="conflicting"):
        await store.create_if_idempotent(conflicting)
    loaded = await store.read(
        job_id=record.job_id,
        attempt_number=record.attempt_number,
        segment_id=record.segment_id,
    )
    assert loaded == first


@pytest.mark.asyncio
async def test_resolver_pins_uncertain_record_and_manifest(tmp_path: Path) -> None:
    record = remote_record_for_status(RemoteSpeechJobStatus.UNCERTAIN)
    remote_store = LocalRemoteSpeechJobStore(tmp_path)
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
    manifest_path = tmp_path / "production" / str(record.job_id) / "generating_narration" / "attempt-1"
    manifest_path.mkdir(parents=True, exist_ok=True)
    (manifest_path / "speech-generation-manifest.json").write_bytes(
        serialize_speech_manifest(manifest)
    )

    resolution = _resolution(record)
    resolver = SpeechUncertaintyResolver(tmp_path)
    assert await resolver.resolve(resolution) is True
    assert await resolver.resolve(resolution) is True

    loaded = deserialize_speech_submission_resolution(
        (manifest_path / "speech-resolutions" / f"{SEGMENT_ID}.json").read_bytes()
    )
    assert loaded.fresh_submission_eligible is True


def test_wrong_fingerprint_cannot_become_retry_eligible() -> None:
    record = remote_record_for_status(RemoteSpeechJobStatus.UNCERTAIN)
    resolution = _resolution(record)
    decision = RemoteSpeechRecoveryPolicy().classify(
        record=record,
        verified_local_audio=False,
        resolution=resolution.model_copy(update={"request_fingerprint": "f" * 64}),
    )
    assert decision.action is RemoteSpeechRecoveryAction.REQUIRE_MANUAL_REVIEW
    assert decision.safe_to_submit is False


def test_resolution_serialization_is_canonical_and_preserves_cost_history() -> None:
    record = remote_record_for_status(RemoteSpeechJobStatus.UNCERTAIN)
    resolution = _resolution(record)
    content = serialize_speech_submission_resolution(resolution)
    assert content.endswith(b"\n")
    assert deserialize_speech_submission_resolution(content) == resolution
    assert resolution.historical_cost_preserved is True
