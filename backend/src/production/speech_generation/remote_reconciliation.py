"""Read-only reconciliation for provider-neutral remote speech records."""

from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from pydantic import Field

from backend.src.production.binary_assets.exceptions import BinaryAssetError
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.domain.base import ContractModel
from backend.src.production.speech_generation.exceptions import (
    SpeechAudioStoreError,
)
from backend.src.production.speech_generation.fingerprinting import (
    SpeechRemoteRequestFingerprintInput,
    speech_remote_request_fingerprint,
)
from backend.src.production.speech_generation.ports import SpeechAudioStore
from backend.src.production.speech_generation.remote_models import (
    RemoteSpeechJobRecord,
    RemoteSpeechJobStatus,
)
from backend.src.production.speech_generation.remote_serialization import (
    deserialize_remote_speech_job,
)


class RemoteSpeechReconciliationIssueKind(StrEnum):
    MISSING_RECORD = "missing_record"
    CORRUPT_RECORD = "corrupt_record"
    FINGERPRINT_MISMATCH = "fingerprint_mismatch"
    CAPABILITY_MISMATCH = "capability_mismatch"
    PRICING_MISMATCH = "pricing_mismatch"
    MISSING_AUTHORIZATION = "missing_authorization"
    ESTIMATE_OVER_AUTHORIZATION = "estimate_over_authorization"
    INVALID_STATE = "invalid_state"
    MISSING_REMOTE_IDENTITY = "missing_remote_identity"
    UNCERTAIN_RETRY_VIOLATION = "uncertain_retry_violation"
    MISSING_LOCAL_OUTPUT = "missing_local_output"
    LOCAL_PROVENANCE_MISMATCH = "local_provenance_mismatch"
    UNSAFE_PATH = "unsafe_path"
    SENSITIVE_METADATA = "sensitive_metadata"
    ORPHAN_RECORD = "orphan_record"


class RemoteSpeechRecordExpectation(ContractModel):
    job_id: UUID
    attempt_number: int = Field(ge=1)
    segment_id: str = Field(pattern=r"^segment-[a-f0-9]{32}$")
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    capability_snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    pricing_snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class RemoteSpeechReconciliationIssue(ContractModel):
    kind: RemoteSpeechReconciliationIssueKind
    relative_path: str
    segment_id: str | None = None
    detail: str


class RemoteSpeechReconciliationReport(ContractModel):
    issues: tuple[RemoteSpeechReconciliationIssue, ...]
    records_checked: int = Field(ge=0)

    @property
    def healthy(self) -> bool:
        return not self.issues


class RemoteSpeechJobReconciler:
    def __init__(
        self,
        *,
        workspace_root: Path,
        audio_store: SpeechAudioStore,
        max_record_bytes: int,
    ) -> None:
        self._confinement = WorkspaceConfinement(workspace_root)
        self._audio_store = audio_store
        self._maximum = max_record_bytes

    async def reconcile(
        self,
        *,
        expectations: tuple[RemoteSpeechRecordExpectation, ...] = (),
    ) -> RemoteSpeechReconciliationReport:
        records, issues = await asyncio.to_thread(self._scan_records)
        expected = {
            (item.job_id, item.attempt_number, item.segment_id): item for item in expectations
        }
        found: set[tuple[UUID, int, str]] = set()
        for record, relative in records:
            key = (record.job_id, record.attempt_number, record.segment_id)
            found.add(key)
            expectation = expected.get(key)
            if expectation is None:
                issues.append(
                    _issue(
                        RemoteSpeechReconciliationIssueKind.ORPHAN_RECORD,
                        relative,
                        record.segment_id,
                        "remote speech record has no expected durable request",
                    )
                )
            else:
                issues.extend(_compare_expectation(record, expectation, relative))
            if record.authorization is None:
                issues.append(
                    _issue(
                        RemoteSpeechReconciliationIssueKind.MISSING_AUTHORIZATION,
                        relative,
                        record.segment_id,
                        "remote speech record lacks explicit cost authorization",
                    )
                )
            if (
                record.status
                in {
                    RemoteSpeechJobStatus.SUBMITTED,
                    RemoteSpeechJobStatus.PENDING,
                    RemoteSpeechJobStatus.PROCESSING,
                }
                and record.generation_mode.value == "asynchronous"
                and record.remote_job_id is None
            ):
                issues.append(
                    _issue(
                        RemoteSpeechReconciliationIssueKind.MISSING_REMOTE_IDENTITY,
                        relative,
                        record.segment_id,
                        "asynchronous speech record lacks durable remote identity",
                    )
                )
            try:
                local = await self._audio_store.resolve(
                    job_id=record.job_id,
                    segment_id=record.segment_id,
                )
            except SpeechAudioStoreError:
                local = None
            if record.status is RemoteSpeechJobStatus.COMPLETED and local is None:
                issues.append(
                    _issue(
                        RemoteSpeechReconciliationIssueKind.MISSING_LOCAL_OUTPUT,
                        relative,
                        record.segment_id,
                        "completed remote speech has no verified local audio",
                    )
                )
            if local is not None and (
                local.asset.metadata.source_script_artifact_id != record.source_script_artifact_id
                or local.asset.metadata.source_script_sha256 != record.source_script_sha256
                or local.asset.metadata.normalized_text_hash != record.normalized_text_hash
                or (record.output is not None and local.asset.sha256 != record.output.sha256)
            ):
                issues.append(
                    _issue(
                        RemoteSpeechReconciliationIssueKind.LOCAL_PROVENANCE_MISMATCH,
                        relative,
                        record.segment_id,
                        "local speech audio provenance differs from remote record",
                    )
                )
        for key, expectation in expected.items():
            if key not in found:
                relative = _expected_relative(expectation)
                issues.append(
                    _issue(
                        RemoteSpeechReconciliationIssueKind.MISSING_RECORD,
                        relative,
                        expectation.segment_id,
                        "expected remote speech record is missing",
                    )
                )
        return RemoteSpeechReconciliationReport(
            issues=tuple(
                sorted(
                    issues,
                    key=lambda item: (
                        item.relative_path,
                        item.kind.value,
                        item.detail,
                    ),
                )
            ),
            records_checked=len(records),
        )

    def _scan_records(
        self,
    ) -> tuple[
        list[tuple[RemoteSpeechJobRecord, str]],
        list[RemoteSpeechReconciliationIssue],
    ]:
        records: list[tuple[RemoteSpeechJobRecord, str]] = []
        issues: list[RemoteSpeechReconciliationIssue] = []
        pattern = "production/*/generating_narration/attempt-*/remote-speech-jobs/*.json"
        for path in sorted(self._confinement.root.glob(pattern)):
            relative = _relative(self._confinement.root, path)
            raw: object = None
            try:
                self._confinement.reject_unsafe_components(path)
                self._confinement.reject_unsafe_file(path)
                if os.stat(path).st_nlink != 1:
                    raise OSError("unsafe hard link")
                content = _read_bounded(path, self._maximum)
            except (BinaryAssetError, OSError, ValueError):
                issues.append(
                    _issue(
                        RemoteSpeechReconciliationIssueKind.UNSAFE_PATH,
                        relative,
                        None,
                        "remote speech record path or link is unsafe",
                    )
                )
                continue
            if _contains_sensitive_key(content):
                issues.append(
                    _issue(
                        RemoteSpeechReconciliationIssueKind.SENSITIVE_METADATA,
                        relative,
                        None,
                        "remote speech record contains a sensitive field",
                    )
                )
            try:
                raw = json.loads(
                    content.decode("utf-8", errors="strict"),
                    parse_constant=_reject_constant,
                    object_pairs_hook=_reject_duplicate_keys,
                )
                record = deserialize_remote_speech_job(content)
            except (UnicodeError, ValueError):
                issues.append(_classify_invalid(relative, raw))
                continue
            if relative != _record_relative(record):
                issues.append(
                    _issue(
                        RemoteSpeechReconciliationIssueKind.UNSAFE_PATH,
                        relative,
                        record.segment_id,
                        "remote speech record path identity differs",
                    )
                )
                continue
            records.append((record, relative))
        return records, issues


def _compare_expectation(
    record: RemoteSpeechJobRecord,
    expected: RemoteSpeechRecordExpectation,
    relative: str,
) -> list[RemoteSpeechReconciliationIssue]:
    issues: list[RemoteSpeechReconciliationIssue] = []
    for kind, actual, wanted, detail in (
        (
            RemoteSpeechReconciliationIssueKind.FINGERPRINT_MISMATCH,
            record.request_fingerprint,
            expected.request_fingerprint,
            "remote speech request fingerprint differs",
        ),
        (
            RemoteSpeechReconciliationIssueKind.CAPABILITY_MISMATCH,
            record.capability_snapshot_hash,
            expected.capability_snapshot_hash,
            "remote speech capability snapshot differs",
        ),
        (
            RemoteSpeechReconciliationIssueKind.PRICING_MISMATCH,
            record.pricing_snapshot_hash,
            expected.pricing_snapshot_hash,
            "remote speech pricing snapshot differs",
        ),
    ):
        if actual != wanted:
            issues.append(_issue(kind, relative, record.segment_id, detail))
    return issues


def _classify_invalid(
    relative: str,
    raw: object,
) -> RemoteSpeechReconciliationIssue:
    kind = RemoteSpeechReconciliationIssueKind.CORRUPT_RECORD
    detail = "remote speech record is corrupt"
    segment_id: str | None = None
    if isinstance(raw, dict):
        candidate = raw.get("segment_id")
        segment_id = candidate if isinstance(candidate, str) else None
        if raw.get("status") == "uncertain" and raw.get("fresh_submission_permitted"):
            kind = RemoteSpeechReconciliationIssueKind.UNCERTAIN_RETRY_VIOLATION
            detail = "uncertain speech submission is incorrectly retryable"
        elif raw.get("authorization") is None:
            kind = RemoteSpeechReconciliationIssueKind.MISSING_AUTHORIZATION
            detail = "remote speech record lacks authorization"
        elif _estimate_exceeds_authorization(raw):
            kind = RemoteSpeechReconciliationIssueKind.ESTIMATE_OVER_AUTHORIZATION
            detail = "remote speech estimate exceeds authorization"
        elif (
            raw.get("generation_mode") == "asynchronous"
            and raw.get("status") in {"submitted", "pending", "processing", "completed"}
            and not raw.get("remote_job_id")
        ):
            kind = RemoteSpeechReconciliationIssueKind.MISSING_REMOTE_IDENTITY
            detail = "asynchronous remote speech identity is missing"
        elif _fingerprint_differs(raw):
            kind = RemoteSpeechReconciliationIssueKind.FINGERPRINT_MISMATCH
            detail = "remote speech request fingerprint differs"
        elif raw.get("request_fingerprint") is not None:
            kind = RemoteSpeechReconciliationIssueKind.INVALID_STATE
            detail = "remote speech record state invariants are invalid"
    return _issue(kind, relative, segment_id, detail)


def _estimate_exceeds_authorization(raw: dict[object, object]) -> bool:
    estimate = raw.get("estimated_cost")
    authorization = raw.get("authorization")
    if not isinstance(estimate, dict) or not isinstance(authorization, dict):
        return False
    try:
        maximum_estimate = Decimal(str(estimate["estimated_maximum_cost"]))
        maximum_authorized = Decimal(str(authorization["maximum_authorized_cost"]))
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return False
    return maximum_estimate > maximum_authorized


def _fingerprint_differs(raw: dict[object, object]) -> bool:
    expectation = raw.get("output_expectation")
    if not isinstance(expectation, dict):
        return False
    try:
        fingerprint_input = SpeechRemoteRequestFingerprintInput.model_validate(
            {
                "source_script_artifact_id": raw["source_script_artifact_id"],
                "source_script_sha256": raw["source_script_sha256"],
                "segment_id": raw["segment_id"],
                "normalized_text_hash": raw["normalized_text_hash"],
                "provider": raw["provider"],
                "model": raw["model"],
                "voice": raw["voice"],
                "language": raw["language"],
                "speaking_rate": raw.get("speaking_rate"),
                "audio_format": expectation["audio_format"],
                "sample_rate_hz": expectation["sample_rate_hz"],
                "channel_count": expectation["channel_count"],
                "capability_snapshot_hash": raw["capability_snapshot_hash"],
                "pricing_snapshot_hash": raw["pricing_snapshot_hash"],
                "generation_mode": raw["generation_mode"],
                "options": raw.get("options", {}),
            }
        )
    except (KeyError, TypeError, ValueError):
        return False
    return speech_remote_request_fingerprint(fingerprint_input) != raw.get("request_fingerprint")


def _issue(
    kind: RemoteSpeechReconciliationIssueKind,
    relative: str,
    segment_id: str | None,
    detail: str,
) -> RemoteSpeechReconciliationIssue:
    return RemoteSpeechReconciliationIssue(
        kind=kind,
        relative_path=relative,
        segment_id=segment_id,
        detail=detail,
    )


def _record_relative(record: RemoteSpeechJobRecord) -> str:
    return (
        f"production/{record.job_id}/generating_narration/"
        f"attempt-{record.attempt_number}/remote-speech-jobs/"
        f"{record.segment_id}.json"
    )


def _expected_relative(expected: RemoteSpeechRecordExpectation) -> str:
    return (
        f"production/{expected.job_id}/generating_narration/"
        f"attempt-{expected.attempt_number}/remote-speech-jobs/"
        f"{expected.segment_id}.json"
    )


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return "<unsafe>"


def _read_bounded(path: Path, maximum: int) -> bytes:
    with path.open("rb") as stream:
        content = stream.read(maximum + 1)
    if not content or len(content) > maximum:
        raise ValueError("remote speech record size is invalid")
    return content


def _contains_sensitive_key(content: bytes) -> bool:
    folded = content.lower()
    return any(
        key in folded
        for key in (
            b'"api_key"',
            b'"authorization_header"',
            b'"credential"',
            b'"password"',
            b'"signed_url"',
            b'"download_url"',
            b'"provider_url"',
            b'"token"',
            b'_token"',
            b'"secret"',
            b"bearer ",
        )
    )


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _reject_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result
