import asyncio
import os
from datetime import timedelta

import pytest
from pydantic import ValidationError

from backend.src.production.speech_generation.exceptions import (
    RemoteSpeechJobConflictError,
    RemoteSpeechJobCorruptError,
    RemoteSpeechJobStoreError,
)
from backend.src.production.speech_generation.remote_capabilities import (
    SpeechRemoteGenerationMode,
)
from backend.src.production.speech_generation.remote_job_store import (
    LocalRemoteSpeechJobStore,
)
from backend.src.production.speech_generation.remote_models import (
    RemoteSpeechJobRecord,
    RemoteSpeechJobStatus,
    validate_remote_speech_job_transition,
)
from backend.src.production.speech_generation.remote_recovery import (
    RemoteSpeechRecoveryAction,
    RemoteSpeechRecoveryPolicy,
)
from backend.src.production.speech_generation.remote_serialization import (
    deserialize_remote_speech_job,
    serialize_remote_speech_job,
)
from backend.tests.unit.production.speech_generation.conftest import JOB_ID, NOW
from backend.tests.unit.production.speech_generation.remote_test_helpers import (
    SEGMENT_ID,
    prepared_remote_record,
    remote_record_for_status,
)


def test_remote_record_is_strict_immutable_timezone_aware_and_has_no_raw_text() -> None:
    record = prepared_remote_record()
    content = serialize_remote_speech_job(record)

    assert b"narration_text" not in content
    assert b"obviously-fake" not in content
    with pytest.raises(ValidationError):
        record.status = RemoteSpeechJobStatus.SUBMITTING
    with pytest.raises(ValidationError):
        RemoteSpeechJobRecord.model_validate(
            {**record.model_dump(mode="python"), "unexpected": True}
        )
    with pytest.raises(ValidationError, match="timezone-aware"):
        RemoteSpeechJobRecord.model_validate(
            {
                **record.model_dump(mode="python"),
                "prepared_at": NOW.replace(tzinfo=None),
            }
        )


def test_remote_record_decimal_round_trip_is_canonical_and_strict() -> None:
    record = prepared_remote_record()
    content = serialize_remote_speech_job(record)

    assert content.endswith(b"\n")
    assert b'"estimated_maximum_cost":"0.026"' in content
    assert deserialize_remote_speech_job(content) == record
    with pytest.raises(ValueError, match="duplicate"):
        deserialize_remote_speech_job(b'{"schema_version":"1.0.0","schema_version":"1.0.0"}')
    with pytest.raises(ValueError, match="constant"):
        deserialize_remote_speech_job(b'{"estimated_cost":NaN}')


def test_remote_job_transition_requires_pre_submission_checkpoint() -> None:
    prepared = prepared_remote_record()
    submitting = remote_record_for_status(RemoteSpeechJobStatus.SUBMITTING)
    submitted = remote_record_for_status(RemoteSpeechJobStatus.SUBMITTED)

    validate_remote_speech_job_transition(prepared, submitting)
    validate_remote_speech_job_transition(submitting, submitted)
    with pytest.raises(ValueError, match="invalid remote speech transition"):
        validate_remote_speech_job_transition(prepared, submitted)


def test_remote_job_generation_modes_keep_polling_illegal_for_synchronous() -> None:
    submitted = remote_record_for_status(RemoteSpeechJobStatus.SUBMITTED)
    with pytest.raises(ValidationError, match="polling states"):
        RemoteSpeechJobRecord.model_validate(
            {
                **submitted.model_dump(mode="python"),
                "generation_mode": SpeechRemoteGenerationMode.SYNCHRONOUS,
            }
        )
    prepared = prepared_remote_record()
    with pytest.raises(ValidationError, match="streaming"):
        RemoteSpeechJobRecord.model_validate(
            {
                **prepared.model_dump(mode="python"),
                "generation_mode": SpeechRemoteGenerationMode.STREAMING,
            }
        )


def test_remote_job_poll_state_is_monotonic_and_terminal_status_is_immutable() -> None:
    processing = remote_record_for_status(RemoteSpeechJobStatus.PROCESSING).model_copy(
        update={"poll_attempts": 2, "last_polled_at": NOW + timedelta(seconds=4)}
    )
    regressed = processing.model_copy(
        update={"poll_attempts": 1, "last_polled_at": NOW + timedelta(seconds=3)}
    )
    with pytest.raises(ValueError, match="poll attempts"):
        validate_remote_speech_job_transition(processing, regressed)

    completed = remote_record_for_status(RemoteSpeechJobStatus.COMPLETED)
    changed = completed.model_copy(
        update={
            "status": RemoteSpeechJobStatus.FAILED,
            "safe_error_code": "changed_after_terminal",
        }
    )
    with pytest.raises(ValueError, match="terminal"):
        validate_remote_speech_job_transition(completed, changed)


@pytest.mark.asyncio
async def test_remote_store_create_read_cas_and_stale_conflict(tmp_path) -> None:
    store = LocalRemoteSpeechJobStore(tmp_path)
    prepared = prepared_remote_record()
    submitting = remote_record_for_status(RemoteSpeechJobStatus.SUBMITTING)

    await store.create(prepared)
    assert (
        await store.read(
            job_id=prepared.job_id,
            attempt_number=prepared.attempt_number,
            segment_id=prepared.segment_id,
        )
        == prepared
    )
    with pytest.raises(RemoteSpeechJobConflictError, match="already exists"):
        await store.create(prepared)
    await store.checkpoint(previous=prepared, current=submitting)
    with pytest.raises(RemoteSpeechJobConflictError, match="concurrently"):
        await store.checkpoint(previous=prepared, current=submitting)


@pytest.mark.asyncio
async def test_remote_store_concurrent_duplicate_creation_has_one_winner(
    tmp_path,
) -> None:
    store = LocalRemoteSpeechJobStore(tmp_path)
    record = prepared_remote_record()
    outcomes = await asyncio.gather(
        store.create(record),
        store.create(record),
        return_exceptions=True,
    )
    assert sum(outcome is None for outcome in outcomes) == 1
    conflicts = [
        outcome for outcome in outcomes if isinstance(outcome, RemoteSpeechJobConflictError)
    ]
    assert len(conflicts) == 1


@pytest.mark.asyncio
async def test_remote_store_rejects_corruption_bounded_reads_and_traversal(tmp_path) -> None:
    store = LocalRemoteSpeechJobStore(tmp_path)
    record = prepared_remote_record()
    await store.create(record)
    path = (
        tmp_path
        / "production"
        / str(JOB_ID)
        / "generating_narration"
        / "attempt-1"
        / "remote-speech-jobs"
        / f"{SEGMENT_ID}.json"
    )
    path.write_bytes(b'{"status":NaN}')
    with pytest.raises(RemoteSpeechJobCorruptError):
        await store.read(job_id=JOB_ID, attempt_number=1, segment_id=SEGMENT_ID)
    path.write_bytes(b"x" * 1_000_001)
    with pytest.raises(RemoteSpeechJobCorruptError):
        await store.read(job_id=JOB_ID, attempt_number=1, segment_id=SEGMENT_ID)
    with pytest.raises(RemoteSpeechJobStoreError, match="identity"):
        await store.read(
            job_id=JOB_ID,
            attempt_number=1,
            segment_id="../unsafe",
        )


@pytest.mark.asyncio
async def test_remote_store_rejects_symlink(tmp_path) -> None:
    store = LocalRemoteSpeechJobStore(tmp_path)
    record = prepared_remote_record()
    await store.create(record)
    path = (
        tmp_path
        / "production"
        / str(JOB_ID)
        / "generating_narration"
        / "attempt-1"
        / "remote-speech-jobs"
        / f"{SEGMENT_ID}.json"
    )
    original = path.with_name("original.json")
    path.replace(original)
    try:
        path.symlink_to(original)
    except OSError:
        pytest.skip("symlinks are unavailable")
    with pytest.raises(RemoteSpeechJobCorruptError):
        await store.read(job_id=JOB_ID, attempt_number=1, segment_id=SEGMENT_ID)


@pytest.mark.asyncio
async def test_remote_store_rejects_hard_link(tmp_path) -> None:
    store = LocalRemoteSpeechJobStore(tmp_path)
    record = prepared_remote_record()
    await store.create(record)
    path = (
        tmp_path
        / "production"
        / str(JOB_ID)
        / "generating_narration"
        / "attempt-1"
        / "remote-speech-jobs"
        / f"{SEGMENT_ID}.json"
    )
    original = path.with_name("original.json")
    path.replace(original)
    try:
        os.link(original, path)
    except OSError:
        pytest.skip("hard links are unavailable")
    with pytest.raises(RemoteSpeechJobCorruptError):
        await store.read(job_id=JOB_ID, attempt_number=1, segment_id=SEGMENT_ID)


@pytest.mark.parametrize(
    ("status", "local", "action", "safe"),
    [
        (
            RemoteSpeechJobStatus.PREPARED,
            False,
            RemoteSpeechRecoveryAction.PROCEED_TO_SUBMISSION,
            True,
        ),
        (
            RemoteSpeechJobStatus.SUBMITTING,
            False,
            RemoteSpeechRecoveryAction.MARK_UNCERTAIN,
            False,
        ),
        (
            RemoteSpeechJobStatus.UNCERTAIN,
            False,
            RemoteSpeechRecoveryAction.REQUIRE_MANUAL_REVIEW,
            False,
        ),
        (
            RemoteSpeechJobStatus.SUBMITTED,
            False,
            RemoteSpeechRecoveryAction.POLL,
            False,
        ),
        (
            RemoteSpeechJobStatus.PROCESSING,
            False,
            RemoteSpeechRecoveryAction.POLL,
            False,
        ),
        (
            RemoteSpeechJobStatus.COMPLETED,
            False,
            RemoteSpeechRecoveryAction.DOWNLOAD,
            False,
        ),
        (
            RemoteSpeechJobStatus.FAILED,
            False,
            RemoteSpeechRecoveryAction.STOP_TERMINAL,
            False,
        ),
        (
            RemoteSpeechJobStatus.SUBMITTING,
            True,
            RemoteSpeechRecoveryAction.RECOVER_LOCAL_AUDIO,
            False,
        ),
    ],
)
def test_remote_recovery_policy_never_resubmits_ambiguous_work(
    status: RemoteSpeechJobStatus,
    local: bool,
    action: RemoteSpeechRecoveryAction,
    safe: bool,
) -> None:
    decision = RemoteSpeechRecoveryPolicy().classify(
        record=remote_record_for_status(status),
        verified_local_audio=local,
    )
    assert decision.action is action
    assert decision.safe_to_submit is safe


def test_prepared_recovery_requires_explicit_cost_authorization() -> None:
    decision = RemoteSpeechRecoveryPolicy().classify(
        record=prepared_remote_record(authorization=None),
        verified_local_audio=False,
    )
    assert decision.action is RemoteSpeechRecoveryAction.REQUIRE_MANUAL_REVIEW
    assert decision.safe_to_submit is False
