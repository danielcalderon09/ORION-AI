"""Read-only reconciliation for durable speech manifests and WAV assets."""

import json
from enum import StrEnum
from pathlib import Path, PurePosixPath
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field

from backend.src.production.binary_assets.exceptions import BinaryAssetLinkError
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.enums import ProductionStage
from backend.src.production.planning.reconciliation import (
    RegisteredPlanningArtifactReader,
)
from backend.src.production.runtime.context import StageContext
from backend.src.production.speech_generation.audio_store import (
    speech_audio_relative_path,
)
from backend.src.production.speech_generation.exceptions import (
    SpeechAudioChecksumError,
    SpeechAudioIntegrityError,
    SpeechAudioLinkError,
    SpeechAudioNotFoundError,
    SpeechAudioPathError,
    SpeechSourceScriptError,
)
from backend.src.production.speech_generation.models import (
    SpeechGenerationManifestStatus,
    SpeechSegmentStatus,
)
from backend.src.production.speech_generation.ports import (
    SpeechAudioStore,
    SpeechSourceScriptReader,
)
from backend.src.production.speech_generation.serialization import (
    deserialize_speech_manifest,
)


class SpeechReconciliationIssueKind(StrEnum):
    MISSING_SOURCE_SCRIPT = "missing_source_script"
    SOURCE_SCRIPT_CHANGED = "source_script_changed"
    MISSING_MANIFEST = "missing_manifest"
    CORRUPT_MANIFEST = "corrupt_manifest"
    DUPLICATE_SEGMENT = "duplicate_segment"
    INVALID_TRANSITION = "invalid_transition"
    MISSING_AUDIO = "missing_audio"
    ORPHAN_AUDIO = "orphan_audio"
    AUDIO_CHECKSUM_MISMATCH = "audio_checksum_mismatch"
    WAV_METADATA_MISMATCH = "wav_metadata_mismatch"
    DURATION_MISMATCH = "duration_mismatch"
    TERMINAL_INCOMPLETE = "terminal_incomplete"
    SENSITIVE_METADATA = "sensitive_metadata"
    UNSAFE_PATH = "unsafe_path"
    LINK_DRIFT = "link_drift"


class SpeechReconciliationIssue(ContractModel):
    kind: SpeechReconciliationIssueKind
    relative_path: str
    detail: str = Field(min_length=1, max_length=300)


class SpeechReconciliationReport(ContractModel):
    scanned: int = Field(default=0, ge=0)
    valid: int = Field(default=0, ge=0)
    issues: tuple[SpeechReconciliationIssue, ...] = ()


class SpeechGenerationReconciler:
    """Inspect speech files and registered paths without changing any state."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        audio_store: SpeechAudioStore,
        source_reader: SpeechSourceScriptReader,
        registered_reader: RegisteredPlanningArtifactReader,
        max_manifest_bytes: int,
    ) -> None:
        self._root = workspace_root.resolve()
        self._confinement = WorkspaceConfinement(workspace_root)
        self._store = audio_store
        self._source_reader = source_reader
        self._registered_reader = registered_reader
        self._maximum = max_manifest_bytes
        self.latest_report = SpeechReconciliationReport()

    async def reconcile(self) -> SpeechReconciliationReport:
        registered = self._registered_reader.list_registered_paths()
        manifests, speech_files = self._scan()
        issues: list[SpeechReconciliationIssue] = []
        referenced_audio: set[str] = set()
        valid = 0
        discovered_manifests: set[str] = set()
        for path in manifests:
            relative = self._relative(path)
            discovered_manifests.add(relative)
            try:
                self._confinement.reject_unsafe_file(path)
                content = _read_bounded(path, self._maximum)
                if len(content) > self._maximum:
                    raise ValueError("speech manifest exceeds configured limit")
                if _contains_sensitive_metadata(content):
                    issues.append(
                        self._issue(
                            SpeechReconciliationIssueKind.SENSITIVE_METADATA,
                            relative,
                            "speech manifest contains sensitive metadata keys",
                        )
                    )
                    continue
                raw_issue = _raw_manifest_issue(content)
                if raw_issue is not None:
                    issues.append(self._issue(raw_issue, relative, _raw_issue_detail(raw_issue)))
                    continue
                manifest = deserialize_speech_manifest(content)
            except BinaryAssetLinkError:
                issues.append(
                    self._issue(
                        SpeechReconciliationIssueKind.LINK_DRIFT,
                        relative,
                        "speech manifest path contains an unsafe link",
                    )
                )
                continue
            except Exception:
                issues.append(
                    self._issue(
                        SpeechReconciliationIssueKind.CORRUPT_MANIFEST,
                        relative,
                        "speech manifest is invalid or unsafe",
                    )
                )
                continue
            source_ok = True
            try:
                source = await self._source_reader.read_for_speech_generation(
                    context=_source_context(relative, manifest.source_script_artifact_id)
                )
                if (
                    source.artifact_id != manifest.source_script_artifact_id
                    or source.sha256 != manifest.source_script_sha256
                    or source.schema_version != manifest.source_script_schema_version
                ):
                    source_ok = False
                    issues.append(
                        self._issue(
                            SpeechReconciliationIssueKind.SOURCE_SCRIPT_CHANGED,
                            relative,
                            "source script identity or checksum differs",
                        )
                    )
            except SpeechSourceScriptError:
                source_ok = False
                issues.append(
                    self._issue(
                        SpeechReconciliationIssueKind.MISSING_SOURCE_SCRIPT,
                        relative,
                        "source script is missing or invalid",
                    )
                )
            identifiers = tuple(entry.segment_id for entry in manifest.entries)
            if len(identifiers) != len(set(identifiers)):
                issues.append(
                    self._issue(
                        SpeechReconciliationIssueKind.DUPLICATE_SEGMENT,
                        relative,
                        "speech manifest contains duplicate segment identities",
                    )
                )
            terminal_incomplete = (
                manifest.status is SpeechGenerationManifestStatus.COMPLETED
                and any(
                    entry.status is not SpeechSegmentStatus.STORED for entry in manifest.entries
                )
            )
            if terminal_incomplete:
                issues.append(
                    self._issue(
                        SpeechReconciliationIssueKind.TERMINAL_INCOMPLETE,
                        relative,
                        "completed speech manifest has incomplete entries",
                    )
                )
            referenced_audio.update(
                record.previous_audio_storage_path for record in manifest.fitting_records
            )
            for entry in manifest.entries:
                if entry.status is not SpeechSegmentStatus.STORED:
                    continue
                assert entry.storage_path is not None
                referenced_audio.add(entry.storage_path)
                if entry.storage_path != speech_audio_relative_path(
                    job_id=manifest.job_id,
                    segment_id=entry.segment_id,
                ):
                    issues.append(
                        self._issue(
                            SpeechReconciliationIssueKind.UNSAFE_PATH,
                            entry.storage_path,
                            "speech manifest entry path is not contractual",
                        )
                    )
                    continue
                try:
                    resolved = await self._store.resolve(
                        job_id=manifest.job_id,
                        segment_id=entry.segment_id,
                    )
                    asset = resolved.asset
                    if (
                        asset.storage_path != entry.storage_path
                        or asset.sha256 != entry.sha256
                        or asset.size_bytes != entry.size_bytes
                    ):
                        issues.append(
                            self._issue(
                                SpeechReconciliationIssueKind.AUDIO_CHECKSUM_MISMATCH,
                                entry.storage_path,
                                "speech asset integrity differs from manifest",
                            )
                        )
                        continue
                    if (
                        asset.sample_rate_hz != entry.sample_rate_hz
                        or asset.channel_count != entry.channel_count
                        or asset.sample_width_bytes != entry.sample_width_bytes
                        or asset.frame_count != entry.frame_count
                    ):
                        issues.append(
                            self._issue(
                                SpeechReconciliationIssueKind.WAV_METADATA_MISMATCH,
                                entry.storage_path,
                                "WAV metadata differs from manifest",
                            )
                        )
                        continue
                    if asset.duration_ms != entry.duration_ms:
                        issues.append(
                            self._issue(
                                SpeechReconciliationIssueKind.DURATION_MISMATCH,
                                entry.storage_path,
                                "WAV duration differs from manifest",
                            )
                        )
                        continue
                    if source_ok:
                        valid += 1
                except SpeechAudioLinkError:
                    issues.append(
                        self._issue(
                            SpeechReconciliationIssueKind.LINK_DRIFT,
                            entry.storage_path,
                            "speech WAV or sidecar has unsafe link drift",
                        )
                    )
                except SpeechAudioPathError:
                    issues.append(
                        self._issue(
                            SpeechReconciliationIssueKind.UNSAFE_PATH,
                            entry.storage_path,
                            "speech WAV path is unsafe",
                        )
                    )
                except SpeechAudioChecksumError:
                    issues.append(
                        self._issue(
                            SpeechReconciliationIssueKind.AUDIO_CHECKSUM_MISMATCH,
                            entry.storage_path,
                            "speech WAV checksum differs from durable metadata",
                        )
                    )
                except SpeechAudioIntegrityError:
                    issues.append(
                        self._issue(
                            SpeechReconciliationIssueKind.WAV_METADATA_MISMATCH,
                            entry.storage_path,
                            "speech WAV or sidecar integrity is invalid",
                        )
                    )
                except SpeechAudioNotFoundError:
                    issues.append(
                        self._issue(
                            SpeechReconciliationIssueKind.MISSING_AUDIO,
                            entry.storage_path,
                            "speech manifest has no matching WAV",
                        )
                    )
                except Exception:
                    issues.append(
                        self._issue(
                            SpeechReconciliationIssueKind.MISSING_AUDIO,
                            entry.storage_path,
                            "speech manifest has no valid matching WAV",
                        )
                    )
        for registered_path in sorted(registered):
            if (
                registered_path.endswith("/speech-generation-manifest.json")
                and registered_path not in discovered_manifests
            ):
                issues.append(
                    self._issue(
                        SpeechReconciliationIssueKind.MISSING_MANIFEST,
                        registered_path,
                        "registered speech manifest is missing",
                    )
                )
        for relative in speech_files:
            if relative.endswith(".asset.json"):
                if relative.removesuffix(".asset.json") not in speech_files:
                    issues.append(
                        self._issue(
                            SpeechReconciliationIssueKind.MISSING_AUDIO,
                            relative,
                            "speech sidecar has no matching WAV",
                        )
                    )
                continue
            if relative not in referenced_audio:
                issues.append(
                    self._issue(
                        SpeechReconciliationIssueKind.ORPHAN_AUDIO,
                        relative,
                        "speech WAV is not referenced by a durable manifest",
                    )
                )
        report = SpeechReconciliationReport(
            scanned=len(manifests) + len(speech_files),
            valid=valid,
            issues=tuple(
                sorted(
                    issues,
                    key=lambda issue: (issue.relative_path, issue.kind.value),
                )
            ),
        )
        self.latest_report = report
        return report

    def _scan(self) -> tuple[tuple[Path, ...], tuple[str, ...]]:
        production = self._root / "production"
        if not production.exists():
            return (), ()
        manifests = tuple(
            sorted(
                production.glob("*/generating_narration/attempt-*/speech-generation-manifest.json")
            )
        )
        speech_files = tuple(
            sorted(
                self._relative(path)
                for path in production.glob("*/assets/speech/*")
                if path.is_file()
            )
        )
        return manifests, speech_files

    def _relative(self, path: Path) -> str:
        return path.relative_to(self._root).as_posix()

    @staticmethod
    def _issue(
        kind: SpeechReconciliationIssueKind,
        relative_path: str,
        detail: str,
    ) -> SpeechReconciliationIssue:
        return SpeechReconciliationIssue(
            kind=kind,
            relative_path=relative_path,
            detail=detail,
        )


def _source_context(relative: str, source_artifact_id: UUID) -> StageContext:
    parts = PurePosixPath(relative).parts
    if (
        len(parts) != 5
        or parts[0] != "production"
        or parts[2] != "generating_narration"
        or not parts[3].startswith("attempt-")
    ):
        raise ValueError("speech manifest path is not contractual")
    job_id = UUID(parts[1])
    attempt = int(parts[3][8:])
    command_id = uuid5(NAMESPACE_URL, f"orion:speech-reconcile:{job_id}:{attempt}")
    return StageContext(
        job_id=job_id,
        command_id=command_id,
        stage=ProductionStage.GENERATING_NARRATION,
        attempt_number=attempt,
        input_artifact_ids=(source_artifact_id,),
        workspace_relative_path=(f"production/{job_id}/generating_narration/attempt-{attempt}"),
        correlation_id=job_id,
    )


def _read_bounded(path: Path, maximum: int) -> bytes:
    with path.open("rb") as stream:
        return stream.read(maximum + 1)


def _contains_sensitive_metadata(content: bytes) -> bool:
    lowered = content.lower()
    return any(
        marker in lowered
        for marker in (
            b'"api_key"',
            b'"authorization"',
            b'"credential"',
            b'"password"',
            b'"secret"',
            b'"token"',
        )
    )


def _raw_manifest_issue(
    content: bytes,
) -> SpeechReconciliationIssueKind | None:
    try:
        payload = json.loads(
            content.decode("utf-8", errors="strict"),
            parse_constant=lambda value: _raise_invalid_constant(value),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return None
    identifiers = [
        entry.get("segment_id")
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("segment_id"), str)
    ]
    if len(identifiers) != len(set(identifiers)):
        return SpeechReconciliationIssueKind.DUPLICATE_SEGMENT
    if payload.get("status") == "completed" and any(
        not isinstance(entry, dict) or entry.get("status") != "stored" for entry in entries
    ):
        return SpeechReconciliationIssueKind.TERMINAL_INCOMPLETE
    return None


def _raw_issue_detail(kind: SpeechReconciliationIssueKind) -> str:
    if kind is SpeechReconciliationIssueKind.DUPLICATE_SEGMENT:
        return "speech manifest contains duplicate segment identities"
    return "terminal speech manifest has incomplete entries"


def _raise_invalid_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result
