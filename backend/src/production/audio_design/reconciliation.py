"""Read-only reconciliation of audio-design manifests and stored WAV assets."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import Field

from backend.src.production.audio_design.asset_store import (
    audio_asset_relative_path,
)
from backend.src.production.audio_design.configuration import (
    AudioDesignConfiguration,
)
from backend.src.production.audio_design.duration import (
    duration_for_frame_count,
    frame_count_for_duration,
)
from backend.src.production.audio_design.exceptions import (
    AudioDesignSourceError,
    AudioDesignStoreIntegrityError,
    AudioDesignStoreNotFoundError,
    AudioDesignStorePathError,
)
from backend.src.production.audio_design.fingerprints import (
    SIMULATED_MUSIC_PROVIDER_ID,
    SIMULATED_SOUND_EFFECT_PROVIDER_ID,
    music_request_fingerprint,
    sound_effect_request_fingerprint,
)
from backend.src.production.audio_design.manifest_store import (
    audio_design_manifest_relative_path,
)
from backend.src.production.audio_design.models import (
    SUPPORTED_AUDIO_DESIGN_MANIFEST_VERSIONS,
    AudioAssetExpectation,
    AudioAssetKind,
    AudioDesignAssetStatus,
    AudioDesignManifestStatus,
    AudioDesignPlan,
    AudioFormatExpectation,
)
from backend.src.production.audio_design.plan import derive_audio_design_plan
from backend.src.production.audio_design.ports import (
    AudioDesignAssetStore,
    AudioDesignSourceScriptReader,
)
from backend.src.production.audio_design.serialization import (
    deserialize_audio_design_manifest,
)
from backend.src.production.binary_assets.exceptions import (
    BinaryAssetLinkError,
    BinaryAssetNotFoundError,
    BinaryAssetPathError,
)
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.domain.base import ContractModel
from backend.src.production.runtime.context import StageContext


class AudioDesignReconciliationIssueKind(StrEnum):
    MISSING_SOURCE_SCRIPT = "missing_source_script"
    MISSING_MANIFEST = "missing_manifest"
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    CORRUPT_MANIFEST = "corrupt_manifest"
    STALE_PLAN = "stale_plan"
    INVALID_STATUS = "invalid_status"
    MISSING_ASSET = "missing_asset"
    UNCOMMITTED_ASSET = "uncommitted_asset"
    ORPHAN_ASSET = "orphan_asset"
    HASH_MISMATCH = "hash_mismatch"
    METADATA_MISMATCH = "metadata_mismatch"
    UNSAFE_PATH = "unsafe_path"
    SENSITIVE_METADATA = "sensitive_metadata"


class AudioDesignReconciliationIssue(ContractModel):
    kind: AudioDesignReconciliationIssueKind
    relative_path: str
    detail: str = Field(min_length=1, max_length=300)


class AudioDesignReconciliationReport(ContractModel):
    manifest_present: bool
    schema_supported: bool
    manifest_status: AudioDesignManifestStatus | None = None
    expected_asset_count: int = Field(ge=0)
    completed_asset_count: int = Field(ge=0)
    missing_assets: int = Field(ge=0)
    orphan_assets: int = Field(ge=0)
    invalid_assets: int = Field(ge=0)
    stale_plan: bool
    recovery_safe: bool
    manual_intervention_required: bool
    stage_complete: bool
    issues: tuple[AudioDesignReconciliationIssue, ...] = ()


class AudioDesignReconciler:
    """Inspect one durable stage attempt without modifying any file."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        script_reader: AudioDesignSourceScriptReader,
        music_store: AudioDesignAssetStore,
        sound_effect_store: AudioDesignAssetStore,
        configuration: AudioDesignConfiguration,
    ) -> None:
        self._confinement = WorkspaceConfinement(workspace_root)
        self._reader = script_reader
        self._music_store = music_store
        self._sound_effect_store = sound_effect_store
        self._configuration = configuration

    async def reconcile(
        self,
        *,
        context: StageContext,
    ) -> AudioDesignReconciliationReport:
        issues: list[AudioDesignReconciliationIssue] = []
        try:
            source = await self._reader.read_for_audio_design(context=context)
            plan = derive_audio_design_plan(
                job_id=context.job_id,
                source_script_artifact_id=source.artifact_id,
                source_script_sha256=source.sha256,
                script=source.script,
                configuration=self._configuration,
            )
        except AudioDesignSourceError:
            issues.append(
                self._issue(
                    AudioDesignReconciliationIssueKind.MISSING_SOURCE_SCRIPT,
                    context.workspace_relative_path,
                    "durable source script is missing or invalid",
                )
            )
            return self._report(issues=issues, manual=True)

        manifest_relative = audio_design_manifest_relative_path(context)
        try:
            target = self._confinement.resolve(
                manifest_relative,
                require_exists=True,
            )
            self._confinement.reject_unsafe_file(target)
            with target.open("rb") as stream:
                content = stream.read(self._configuration.max_manifest_bytes + 1)
            if len(content) > self._configuration.max_manifest_bytes:
                raise ValueError("audio-design manifest exceeds configured limit")
        except (FileNotFoundError, BinaryAssetNotFoundError):
            issues.append(
                self._issue(
                    AudioDesignReconciliationIssueKind.MISSING_MANIFEST,
                    manifest_relative,
                    "audio-design manifest is missing",
                )
            )
            return self._report(
                issues=issues,
                expected=self._expected_count(plan),
                recovery_safe=True,
            )
        except (BinaryAssetLinkError, BinaryAssetPathError, OSError, ValueError):
            issues.append(
                self._issue(
                    AudioDesignReconciliationIssueKind.UNSAFE_PATH,
                    manifest_relative,
                    "audio-design manifest path is unsafe",
                )
            )
            return self._report(
                issues=issues,
                expected=self._expected_count(plan),
                manual=True,
            )

        if _contains_sensitive_metadata(content):
            issues.append(
                self._issue(
                    AudioDesignReconciliationIssueKind.SENSITIVE_METADATA,
                    manifest_relative,
                    "audio-design manifest contains sensitive metadata",
                )
            )
        raw_ok, raw_version = _raw_schema_version(content)
        if not raw_ok:
            issues.append(
                self._issue(
                    AudioDesignReconciliationIssueKind.CORRUPT_MANIFEST,
                    manifest_relative,
                    "audio-design manifest is corrupt",
                )
            )
            return self._report(
                issues=issues,
                manifest_present=True,
                expected=self._expected_count(plan),
                manual=True,
            )
        if raw_version not in SUPPORTED_AUDIO_DESIGN_MANIFEST_VERSIONS:
            issues.append(
                self._issue(
                    AudioDesignReconciliationIssueKind.UNSUPPORTED_SCHEMA,
                    manifest_relative,
                    "audio-design manifest schema is unsupported",
                )
            )
            return self._report(
                issues=issues,
                manifest_present=True,
                expected=self._expected_count(plan),
                manual=True,
            )
        try:
            manifest = deserialize_audio_design_manifest(content)
        except (UnicodeError, ValueError, TypeError):
            issues.append(
                self._issue(
                    AudioDesignReconciliationIssueKind.CORRUPT_MANIFEST,
                    manifest_relative,
                    "audio-design manifest is corrupt",
                )
            )
            return self._report(
                issues=issues,
                manifest_present=True,
                expected=self._expected_count(plan),
                manual=True,
            )

        stale = (
            manifest.audio_design_plan_fingerprint != plan.plan_fingerprint
            or manifest.production_script_fingerprint != plan.production_script_fingerprint
        )
        if stale:
            issues.append(
                self._issue(
                    AudioDesignReconciliationIssueKind.STALE_PLAN,
                    manifest_relative,
                    "manifest plan fingerprint differs from durable script",
                )
            )

        expectations = self._expectations(plan)
        expected_paths = {
            audio_asset_relative_path(expectation) for expectation in expectations.values()
        }
        completed = 0
        missing = 0
        invalid = 0
        for entry in manifest.entries:
            expectation = expectations.get(entry.requirement_id)
            if (
                expectation is None
                or expectation.request_fingerprint != entry.request_fingerprint
                or expectation.kind is not entry.kind
            ):
                invalid += 1
                issues.append(
                    self._issue(
                        AudioDesignReconciliationIssueKind.METADATA_MISMATCH,
                        manifest_relative,
                        f"entry identity differs: {entry.requirement_id}",
                    )
                )
                continue
            store = (
                self._music_store
                if entry.kind is AudioAssetKind.MUSIC
                else self._sound_effect_store
            )
            try:
                resolved = await store.resolve(expectation=expectation)
                asset = resolved.asset
                if entry.status is AudioDesignAssetStatus.STORED:
                    if (
                        entry.storage_path != asset.storage_path
                        or entry.sha256 != asset.sha256
                        or entry.size_bytes != asset.size_bytes
                        or entry.expected_audio.duration_ms != asset.audio.duration_ms
                        or entry.expected_audio.frame_count != asset.audio.frame_count
                    ):
                        invalid += 1
                        issues.append(
                            self._issue(
                                AudioDesignReconciliationIssueKind.HASH_MISMATCH,
                                asset.storage_path,
                                "stored WAV differs from manifest checkpoint",
                            )
                        )
                    else:
                        completed += 1
                else:
                    issues.append(
                        self._issue(
                            AudioDesignReconciliationIssueKind.UNCOMMITTED_ASSET,
                            asset.storage_path,
                            "valid deterministic WAV lacks a stored checkpoint",
                        )
                    )
            except AudioDesignStoreNotFoundError:
                missing += 1
                issues.append(
                    self._issue(
                        AudioDesignReconciliationIssueKind.MISSING_ASSET,
                        audio_asset_relative_path(expectation),
                        (
                            "manifest checkpoint has no matching WAV"
                            if entry.status is AudioDesignAssetStatus.STORED
                            else "expected audio-design WAV is not present"
                        ),
                    )
                )
            except AudioDesignStoreIntegrityError:
                invalid += 1
                issues.append(
                    self._issue(
                        AudioDesignReconciliationIssueKind.HASH_MISMATCH,
                        audio_asset_relative_path(expectation),
                        "audio-design WAV is corrupt",
                    )
                )
            except AudioDesignStorePathError:
                invalid += 1
                issues.append(
                    self._issue(
                        AudioDesignReconciliationIssueKind.UNSAFE_PATH,
                        audio_asset_relative_path(expectation),
                        "audio-design WAV path is unsafe",
                    )
                )

        orphan_paths = self._orphan_paths(context, expected_paths)
        issues.extend(
            self._issue(
                AudioDesignReconciliationIssueKind.ORPHAN_ASSET,
                path,
                "audio-design WAV is not expected by this plan",
            )
            for path in orphan_paths
        )
        terminal_incomplete = (
            manifest.status is AudioDesignManifestStatus.COMPLETE
            and completed != len(manifest.entries)
        )
        if terminal_incomplete:
            issues.append(
                self._issue(
                    AudioDesignReconciliationIssueKind.INVALID_STATUS,
                    manifest_relative,
                    "complete manifest does not have all valid assets",
                )
            )
        manual = (
            stale
            or invalid > 0
            or any(
                issue.kind
                in {
                    AudioDesignReconciliationIssueKind.SENSITIVE_METADATA,
                    AudioDesignReconciliationIssueKind.UNSAFE_PATH,
                }
                for issue in issues
            )
        )
        stage_complete = (
            manifest.status is AudioDesignManifestStatus.COMPLETE
            and completed == len(manifest.entries)
            and not manual
            and not orphan_paths
        )
        return AudioDesignReconciliationReport(
            manifest_present=True,
            schema_supported=True,
            manifest_status=manifest.status,
            expected_asset_count=len(manifest.entries),
            completed_asset_count=completed,
            missing_assets=missing,
            orphan_assets=len(orphan_paths),
            invalid_assets=invalid,
            stale_plan=stale,
            recovery_safe=not manual,
            manual_intervention_required=manual,
            stage_complete=stage_complete,
            issues=tuple(sorted(issues, key=lambda issue: (issue.relative_path, issue.kind.value))),
        )

    def _expectations(
        self,
        plan: AudioDesignPlan,
    ) -> dict[str, AudioAssetExpectation]:
        result: dict[str, AudioAssetExpectation] = {}
        if plan.music_requirement is not None:
            item = plan.music_requirement
            fingerprint = music_request_fingerprint(
                item,
                sample_rate_hz=self._configuration.sample_rate_hz,
                channel_count=self._configuration.channel_count,
                sample_width_bytes=self._configuration.sample_width_bytes,
            )
            result[item.requirement_id] = self._expectation(
                plan.job_id,
                AudioAssetKind.MUSIC,
                item.requirement_id,
                fingerprint,
                item.target_duration_ms,
                SIMULATED_MUSIC_PROVIDER_ID,
            )
        for sound_effect_requirement in plan.sound_effect_requirements:
            fingerprint = sound_effect_request_fingerprint(
                sound_effect_requirement,
                sample_rate_hz=self._configuration.sample_rate_hz,
                channel_count=self._configuration.channel_count,
                sample_width_bytes=self._configuration.sample_width_bytes,
            )
            result[sound_effect_requirement.requirement_id] = self._expectation(
                plan.job_id,
                AudioAssetKind.SOUND_EFFECT,
                sound_effect_requirement.requirement_id,
                fingerprint,
                sound_effect_requirement.target_duration_ms,
                SIMULATED_SOUND_EFFECT_PROVIDER_ID,
            )
        return result

    def _expectation(
        self,
        job_id: UUID,
        kind: AudioAssetKind,
        requirement_id: str,
        fingerprint: str,
        duration_ms: int,
        provider_id: str,
    ) -> AudioAssetExpectation:
        frames = frame_count_for_duration(
            duration_ms,
            self._configuration.sample_rate_hz,
        )
        return AudioAssetExpectation(
            job_id=job_id,
            kind=kind,
            requirement_id=requirement_id,
            request_fingerprint=fingerprint,
            provider_id=provider_id,
            audio=AudioFormatExpectation(
                duration_ms=duration_for_frame_count(
                    frames,
                    self._configuration.sample_rate_hz,
                ),
                frame_count=frames,
            ),
        )

    def _orphan_paths(
        self,
        context: StageContext,
        expected_paths: set[str],
    ) -> tuple[str, ...]:
        job_root = self._confinement.root / "production" / str(context.job_id) / "assets"
        paths: list[str] = []
        for directory in ("music", "sound-effects"):
            root = job_root / directory
            if not root.exists():
                continue
            for path in root.glob("*.wav"):
                relative = path.relative_to(self._confinement.root).as_posix()
                if relative not in expected_paths:
                    paths.append(relative)
        return tuple(sorted(paths))

    @staticmethod
    def _expected_count(plan: AudioDesignPlan) -> int:
        return (1 if plan.music_requirement is not None else 0) + len(
            plan.sound_effect_requirements
        )

    @staticmethod
    def _issue(
        kind: AudioDesignReconciliationIssueKind,
        relative_path: str,
        detail: str,
    ) -> AudioDesignReconciliationIssue:
        return AudioDesignReconciliationIssue(
            kind=kind,
            relative_path=relative_path,
            detail=detail,
        )

    @staticmethod
    def _report(
        *,
        issues: list[AudioDesignReconciliationIssue],
        manifest_present: bool = False,
        expected: int = 0,
        recovery_safe: bool = False,
        manual: bool = False,
    ) -> AudioDesignReconciliationReport:
        return AudioDesignReconciliationReport(
            manifest_present=manifest_present,
            schema_supported=not any(
                issue.kind is AudioDesignReconciliationIssueKind.UNSUPPORTED_SCHEMA
                for issue in issues
            ),
            expected_asset_count=expected,
            completed_asset_count=0,
            missing_assets=sum(
                issue.kind is AudioDesignReconciliationIssueKind.MISSING_ASSET for issue in issues
            ),
            orphan_assets=0,
            invalid_assets=sum(
                issue.kind
                in {
                    AudioDesignReconciliationIssueKind.CORRUPT_MANIFEST,
                    AudioDesignReconciliationIssueKind.HASH_MISMATCH,
                    AudioDesignReconciliationIssueKind.METADATA_MISMATCH,
                    AudioDesignReconciliationIssueKind.UNSAFE_PATH,
                }
                for issue in issues
            ),
            stale_plan=False,
            recovery_safe=recovery_safe,
            manual_intervention_required=manual,
            stage_complete=False,
            issues=tuple(issues),
        )


def _raw_schema_version(content: bytes) -> tuple[bool, str | None]:
    try:
        payload = json.loads(
            content.decode("utf-8", errors="strict"),
            parse_constant=lambda value: _raise_invalid_constant(value),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, ValueError, TypeError):
        return False, None
    if not isinstance(payload, dict):
        return False, None
    version = payload.get("schema_version")
    return True, version if isinstance(version, str) else None


def _raise_invalid_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


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
            b'"url"',
        )
    )
