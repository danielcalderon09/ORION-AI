"""Durable, job-scoped budget overlay for safely retryable video recovery."""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.cost_accounting.durable_reader import (
    derive_durable_job_cost_summary,
)
from backend.src.production.cost_accounting.models import JobCostCategory
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.planning.aggregate_visual_budget import (
    AggregateVisualBudgetPlan,
    deserialize_aggregate_visual_budget_plan,
)
from backend.src.production.video_clip_generation.hybrid_generation import (
    HybridVideoEntryStatus,
    HybridVideoGenerationManifest,
    HybridVideoManifestStatus,
    deserialize_hybrid_video_manifest,
)
from backend.src.production.video_clip_generation.providers.openrouter_models import (
    OpenRouterVideoRequestStatus,
)
from backend.src.production.video_clip_generation.retry_budget_models import (
    VideoRetryBudgetAuthorization,
    deserialize_video_retry_budget_authorization,
    serialize_video_retry_budget_authorization,
)
from backend.src.production.video_clip_generation.serialization import (
    deserialize_remote_video_job,
)


class VideoRetryBudgetAuthorizationError(RuntimeError):
    """Fail-closed authorization or recovery-overlay validation failure."""


@dataclass(frozen=True, slots=True)
class _RecoveryEvidence:
    budget: AggregateVisualBudgetPlan
    manifest: HybridVideoGenerationManifest
    accounted_video_cost_usd: Decimal
    accounted_image_cost_usd: Decimal
    provider_requests_consumed: int
    required_provider_requests: int
    required_estimated_cost_usd: Decimal

    @property
    def required_video_ceiling_usd(self) -> Decimal:
        return self.accounted_video_cost_usd + self.required_estimated_cost_usd

    @property
    def projected_visual_cost_usd(self) -> Decimal:
        return self.accounted_image_cost_usd + self.required_video_ceiling_usd


class FilesystemVideoRetryBudgetAuthorizationStore:
    """Create and validate one immutable overlay for one retry lineage."""

    def __init__(self, workspace_root: Path, *, maximum_bytes: int = 100_000) -> None:
        if not 4_096 <= maximum_bytes <= 1_000_000:
            raise ValueError("video retry authorization size limit is invalid")
        self._root = workspace_root
        self._confinement = WorkspaceConfinement(workspace_root)
        self._maximum = maximum_bytes
        self._maximum_source_bytes = 20_000_000

    async def authorize(
        self,
        *,
        job_id: UUID,
        job_status: ProductionJobStatus,
        current_stage: ProductionStage,
        error_code: str | None,
        source_stage_attempt: int,
        target_stage_attempt: int,
        new_authorized_video_job_cost_usd: Decimal,
        maximum_additional_provider_requests: int,
        maximum_additional_estimated_provider_cost_usd: Decimal,
        current_settings_video_job_ceiling_usd: Decimal,
        operator_id: str,
        clock: Callable[[], datetime],
    ) -> tuple[VideoRetryBudgetAuthorization, bool]:
        return await asyncio.to_thread(
            self._authorize_sync,
            job_id,
            job_status,
            current_stage,
            error_code,
            source_stage_attempt,
            target_stage_attempt,
            new_authorized_video_job_cost_usd,
            maximum_additional_provider_requests,
            maximum_additional_estimated_provider_cost_usd,
            current_settings_video_job_ceiling_usd,
            operator_id,
            clock,
        )

    async def require_for_recovery(
        self,
        *,
        job_id: UUID,
        target_stage_attempt: int,
        current_settings_video_job_ceiling_usd: Decimal,
    ) -> Decimal:
        return await asyncio.to_thread(
            self._require_for_recovery_sync,
            job_id,
            target_stage_attempt,
            current_settings_video_job_ceiling_usd,
        )

    async def read(
        self, *, job_id: UUID, target_stage_attempt: int
    ) -> VideoRetryBudgetAuthorization | None:
        return await asyncio.to_thread(self._read_sync, job_id, target_stage_attempt)

    def _authorize_sync(
        self,
        job_id: UUID,
        job_status: ProductionJobStatus,
        current_stage: ProductionStage,
        error_code: str | None,
        source_attempt: int,
        target_attempt: int,
        new_ceiling: Decimal,
        maximum_additional_requests: int,
        maximum_additional_cost: Decimal,
        settings_ceiling: Decimal,
        operator_id: str,
        clock: Callable[[], datetime],
    ) -> tuple[VideoRetryBudgetAuthorization, bool]:
        if job_status is not ProductionJobStatus.WAITING_FOR_RETRY:
            raise VideoRetryBudgetAuthorizationError("job is not waiting for a durable retry")
        if current_stage is not ProductionStage.GENERATING_VIDEO_CLIPS:
            raise VideoRetryBudgetAuthorizationError("job is not in video generation recovery")
        if error_code != "hybrid_video_provider_transient":
            raise VideoRetryBudgetAuthorizationError(
                "video recovery failure is not safely retryable"
            )
        if target_attempt != source_attempt + 1:
            raise VideoRetryBudgetAuthorizationError(
                "video retry target attempt differs from recovery lineage"
            )
        evidence = self._evidence(job_id, source_attempt, require_retryable_failure=True)
        self._validate_requested_capacity(
            evidence,
            new_ceiling=new_ceiling,
            maximum_additional_requests=maximum_additional_requests,
            maximum_additional_cost=maximum_additional_cost,
            settings_ceiling=settings_ceiling,
        )
        path = self._authorization_path(job_id, target_attempt)
        existing = self._read_path(path)
        if existing is not None:
            self._validate_authorization(
                existing,
                evidence=evidence,
                target_attempt=target_attempt,
                settings_ceiling=settings_ceiling,
            )
            if (
                existing.new_authorized_video_job_cost_usd == new_ceiling
                and existing.maximum_additional_provider_requests == maximum_additional_requests
                and existing.maximum_additional_estimated_provider_cost_usd
                == maximum_additional_cost
                and existing.operator_id == operator_id
            ):
                return existing, True
            raise VideoRetryBudgetAuthorizationError(
                "a conflicting video retry authorization already exists"
            )
        authorization = VideoRetryBudgetAuthorization.create(
            job_id=job_id,
            source_stage_attempt=source_attempt,
            target_stage_attempt=target_attempt,
            original_aggregate_visual_budget_fingerprint=evidence.budget.fingerprint,
            original_video_generation_manifest_fingerprint=evidence.manifest.fingerprint,
            current_accounted_video_cost_usd=evidence.accounted_video_cost_usd,
            current_accounted_image_cost_usd=evidence.accounted_image_cost_usd,
            original_authorized_video_job_cost_usd=(
                evidence.budget.maximum_authorized_video_cost_usd
            ),
            new_authorized_video_job_cost_usd=new_ceiling,
            ceiling_increase_usd=(new_ceiling - evidence.budget.maximum_authorized_video_cost_usd),
            maximum_additional_provider_requests=maximum_additional_requests,
            maximum_additional_estimated_provider_cost_usd=maximum_additional_cost,
            provider_requests_already_consumed=evidence.provider_requests_consumed,
            maximum_total_provider_requests=evidence.budget.maximum_video_requests,
            maximum_authorized_cost_per_request_usd=(
                evidence.budget.maximum_authorized_video_cost_per_request_usd
            ),
            durable_total_visual_cost_ceiling_usd=(
                evidence.budget.maximum_authorized_total_visual_cost_usd
            ),
            projected_worst_case_visual_cost_usd=evidence.projected_visual_cost_usd,
            settings_video_job_ceiling_at_authorization_usd=settings_ceiling,
            authorized_at=clock(),
            operator_id=operator_id,
        )
        self._atomic_create(path, serialize_video_retry_budget_authorization(authorization))
        return authorization, False

    def _require_for_recovery_sync(
        self,
        job_id: UUID,
        target_attempt: int,
        settings_ceiling: Decimal,
    ) -> Decimal:
        if target_attempt < 2:
            raise VideoRetryBudgetAuthorizationError(
                "video retry target attempt must be at least two"
            )
        evidence = self._evidence(
            job_id, target_attempt - 1, require_retryable_failure=False
        )
        if evidence.required_video_ceiling_usd <= evidence.budget.maximum_authorized_video_cost_usd:
            return evidence.budget.maximum_authorized_video_cost_usd
        self._validate_retryable_failure(job_id, target_attempt - 1, evidence.manifest)
        authorization = self._read_path(self._authorization_path(job_id, target_attempt))
        if authorization is None:
            raise VideoRetryBudgetAuthorizationError(
                "video retry exceeds the durable budget without an authorization"
            )
        self._validate_authorization(
            authorization,
            evidence=evidence,
            target_attempt=target_attempt,
            settings_ceiling=settings_ceiling,
        )
        return authorization.new_authorized_video_job_cost_usd

    def _read_sync(self, job_id: UUID, target_attempt: int) -> VideoRetryBudgetAuthorization | None:
        authorization = self._read_path(self._authorization_path(job_id, target_attempt))
        if authorization is None:
            return None
        evidence = self._evidence(
            job_id, target_attempt - 1, require_retryable_failure=True
        )
        self._validate_authorization(
            authorization,
            evidence=evidence,
            target_attempt=target_attempt,
            settings_ceiling=authorization.settings_video_job_ceiling_at_authorization_usd,
        )
        return authorization

    def _validate_requested_capacity(
        self,
        evidence: _RecoveryEvidence,
        *,
        new_ceiling: Decimal,
        maximum_additional_requests: int,
        maximum_additional_cost: Decimal,
        settings_ceiling: Decimal,
    ) -> None:
        if maximum_additional_requests != evidence.required_provider_requests:
            raise VideoRetryBudgetAuthorizationError(
                "video retry request authorization differs from required recovery"
            )
        if maximum_additional_cost != evidence.required_estimated_cost_usd:
            raise VideoRetryBudgetAuthorizationError(
                "video retry cost authorization differs from required recovery"
            )
        if new_ceiling != evidence.required_video_ceiling_usd:
            raise VideoRetryBudgetAuthorizationError(
                "video retry ceiling is not the minimum required recovery ceiling"
            )
        if new_ceiling <= evidence.budget.maximum_authorized_video_cost_usd:
            raise VideoRetryBudgetAuthorizationError(
                "video retry does not require a recovery budget overlay"
            )
        if new_ceiling > settings_ceiling:
            raise VideoRetryBudgetAuthorizationError(
                "video retry exceeds the current global Settings ceiling"
            )
        if evidence.provider_requests_consumed + maximum_additional_requests > (
            evidence.budget.maximum_video_requests
        ):
            raise VideoRetryBudgetAuthorizationError(
                "video retry would exceed the durable request count"
            )
        if evidence.projected_visual_cost_usd > (
            evidence.budget.maximum_authorized_total_visual_cost_usd
        ):
            raise VideoRetryBudgetAuthorizationError(
                "video retry would exceed the durable total visual ceiling"
            )

    def _validate_authorization(
        self,
        authorization: VideoRetryBudgetAuthorization,
        *,
        evidence: _RecoveryEvidence,
        target_attempt: int,
        settings_ceiling: Decimal,
    ) -> None:
        if authorization.job_id != evidence.budget.job_id:
            raise VideoRetryBudgetAuthorizationError("video retry job identity drifted")
        if (
            authorization.source_stage_attempt != target_attempt - 1
            or authorization.target_stage_attempt != target_attempt
        ):
            raise VideoRetryBudgetAuthorizationError("video retry attempt lineage drifted")
        expected = {
            "original_aggregate_visual_budget_fingerprint": evidence.budget.fingerprint,
            "original_video_generation_manifest_fingerprint": evidence.manifest.fingerprint,
            "current_accounted_video_cost_usd": evidence.accounted_video_cost_usd,
            "current_accounted_image_cost_usd": evidence.accounted_image_cost_usd,
            "original_authorized_video_job_cost_usd": (
                evidence.budget.maximum_authorized_video_cost_usd
            ),
            "maximum_additional_provider_requests": evidence.required_provider_requests,
            "maximum_additional_estimated_provider_cost_usd": (
                evidence.required_estimated_cost_usd
            ),
            "provider_requests_already_consumed": evidence.provider_requests_consumed,
            "maximum_total_provider_requests": evidence.budget.maximum_video_requests,
            "maximum_authorized_cost_per_request_usd": (
                evidence.budget.maximum_authorized_video_cost_per_request_usd
            ),
            "durable_total_visual_cost_ceiling_usd": (
                evidence.budget.maximum_authorized_total_visual_cost_usd
            ),
            "projected_worst_case_visual_cost_usd": evidence.projected_visual_cost_usd,
        }
        for field, value in expected.items():
            if getattr(authorization, field) != value:
                raise VideoRetryBudgetAuthorizationError(
                    f"video retry authorization {field} drifted"
                )
        if authorization.new_authorized_video_job_cost_usd != (evidence.required_video_ceiling_usd):
            raise VideoRetryBudgetAuthorizationError(
                "video retry authorized ceiling differs from required exposure"
            )
        if authorization.new_authorized_video_job_cost_usd > settings_ceiling:
            raise VideoRetryBudgetAuthorizationError(
                "current Settings no longer permit the video retry ceiling"
            )

    def _evidence(
        self,
        job_id: UUID,
        source_attempt: int,
        *,
        require_retryable_failure: bool,
    ) -> _RecoveryEvidence:
        budget = self._load_budget(job_id)
        manifest = self._load_manifest(job_id, source_attempt)
        if manifest.job_id != job_id or budget.job_id != job_id:
            raise VideoRetryBudgetAuthorizationError("video retry source job differs")
        if manifest.budget_fingerprint != budget.fingerprint:
            raise VideoRetryBudgetAuthorizationError("video retry budget fingerprint drifted")
        if manifest.status is HybridVideoManifestStatus.UNCERTAIN or any(
            entry.status is HybridVideoEntryStatus.UNCERTAIN for entry in manifest.entries
        ):
            raise VideoRetryBudgetAuthorizationError(
                "uncertain video submission cannot use retry budget authorization"
            )
        unresolved = tuple(
            entry
            for entry in manifest.entries
            if entry.status
            in {HybridVideoEntryStatus.PENDING, HybridVideoEntryStatus.FAILED_TRANSIENT}
        )
        for entry in unresolved:
            if entry.estimated_cost_usd > (budget.maximum_authorized_video_cost_per_request_usd):
                raise VideoRetryBudgetAuthorizationError(
                    "video retry request exceeds the durable per-request ceiling"
                )
        summary = derive_durable_job_cost_summary(
            job_id=job_id,
            job_root=self._job_root(job_id),
        )
        video = summary.category(JobCostCategory.VIDEO)
        images = summary.category(JobCostCategory.IMAGES)
        evidence = _RecoveryEvidence(
            budget=budget,
            manifest=manifest,
            accounted_video_cost_usd=video.accounted_cost_usd,
            accounted_image_cost_usd=images.accounted_cost_usd,
            provider_requests_consumed=video.request_count,
            required_provider_requests=len(unresolved),
            required_estimated_cost_usd=sum(
                (entry.estimated_cost_usd for entry in unresolved), Decimal(0)
            ),
        )
        if require_retryable_failure:
            self._validate_retryable_failure(job_id, source_attempt, manifest)
        return evidence

    def _validate_retryable_failure(
        self,
        job_id: UUID,
        source_attempt: int,
        manifest: HybridVideoGenerationManifest,
    ) -> None:
        if manifest.status is not HybridVideoManifestStatus.FAILED:
            raise VideoRetryBudgetAuthorizationError(
                "video retry source manifest is not failed"
            )
        if any(
            entry.status is HybridVideoEntryStatus.FAILED_PERMANENT
            for entry in manifest.entries
        ):
            raise VideoRetryBudgetAuthorizationError(
                "permanent video failure cannot use retry budget authorization"
            )
        if not any(
            entry.status is HybridVideoEntryStatus.FAILED_TRANSIENT
            for entry in manifest.entries
        ):
            raise VideoRetryBudgetAuthorizationError(
                "video retry source has no transient provider failure"
            )
        self._validate_remote_submission_outcomes(job_id, source_attempt, manifest)

    def _validate_remote_submission_outcomes(
        self,
        job_id: UUID,
        source_attempt: int,
        manifest: HybridVideoGenerationManifest,
    ) -> None:
        remote_dir = self._resolve(
            f"production/{job_id}/generating_video_clips/attempt-{source_attempt}/remote-jobs"
        )
        records = []
        if remote_dir.exists():
            for path in sorted(remote_dir.glob("*.json")):
                records.append(deserialize_remote_video_job(self._read(path)))
        if any(
            record.request_status
            in {OpenRouterVideoRequestStatus.SUBMITTING, OpenRouterVideoRequestStatus.UNCERTAIN}
            for record in records
        ):
            raise VideoRetryBudgetAuthorizationError(
                "video retry contains an uncertain provider submission"
            )
        for entry in manifest.entries:
            if entry.status is not HybridVideoEntryStatus.FAILED_TRANSIENT:
                continue
            matching = tuple(
                record for record in records if record.visual_asset_id == entry.visual_asset_id
            )
            if len(matching) != 1:
                raise VideoRetryBudgetAuthorizationError(
                    "transient video failure lacks one durable provider record"
                )
            record = matching[0]
            if (
                record.request_status is not OpenRouterVideoRequestStatus.FAILED
                or record.submission_http_status is None
                or not _is_safely_retryable_http_status(record.submission_http_status)
            ):
                raise VideoRetryBudgetAuthorizationError(
                    "transient video submission outcome is not known"
                )

    def _load_budget(self, job_id: UUID) -> AggregateVisualBudgetPlan:
        base = self._resolve(f"production/{job_id}/visual_asset_planning")
        targets = tuple(base.glob("attempt-*/aggregate-visual-budget-plan.json"))
        if not targets:
            raise VideoRetryBudgetAuthorizationError("aggregate visual budget is missing")
        return deserialize_aggregate_visual_budget_plan(
            self._read(max(targets, key=_attempt_key), source=True)
        )

    def _load_manifest(self, job_id: UUID, source_attempt: int) -> HybridVideoGenerationManifest:
        path = self._resolve(
            f"production/{job_id}/generating_video_clips/attempt-{source_attempt}/"
            "hybrid-video-generation-manifest.json"
        )
        return deserialize_hybrid_video_manifest(self._read(path, source=True))

    def _read_path(self, path: Path) -> VideoRetryBudgetAuthorization | None:
        if not path.exists():
            return None
        try:
            return deserialize_video_retry_budget_authorization(self._read(path))
        except (UnicodeError, ValueError, TypeError) as exc:
            raise VideoRetryBudgetAuthorizationError(
                "video retry authorization is invalid"
            ) from exc

    def _authorization_path(self, job_id: UUID, target_attempt: int) -> Path:
        if target_attempt < 2:
            raise VideoRetryBudgetAuthorizationError(
                "video retry target attempt must be at least two"
            )
        return self._resolve(
            f"production/{job_id}/generating_video_clips/attempt-{target_attempt}/"
            "video-retry-budget-authorization.json"
        )

    def _job_root(self, job_id: UUID) -> Path:
        return self._resolve(f"production/{job_id}")

    def _resolve(self, relative: str) -> Path:
        try:
            return self._confinement.resolve(relative)
        except Exception as exc:
            raise VideoRetryBudgetAuthorizationError("video retry path is unsafe") from exc

    def _read(self, path: Path, *, source: bool = False) -> bytes:
        try:
            self._confinement.reject_unsafe_file(path)
            content = path.read_bytes()
            limit = self._maximum_source_bytes if source else self._maximum
            if not content or len(content) > limit:
                raise ValueError("video retry artifact size is invalid")
            return content
        except Exception as exc:
            raise VideoRetryBudgetAuthorizationError(
                "video retry source artifact is invalid"
            ) from exc

    def _atomic_create(self, path: Path, content: bytes) -> None:
        if not content or len(content) > self._maximum:
            raise VideoRetryBudgetAuthorizationError("video retry authorization size is invalid")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._confinement.reject_unsafe_components(path.parent)
        lock = path.with_name(".video-retry-budget.lock")
        descriptor = -1
        temporary: Path | None = None
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(descriptor)
            descriptor = -1
            if path.exists():
                raise VideoRetryBudgetAuthorizationError(
                    "video retry authorization changed concurrently"
                )
            descriptor, name = tempfile.mkstemp(
                dir=path.parent,
                prefix=".video-retry-budget-",
                suffix=".tmp",
            )
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
        except FileExistsError as exc:
            raise VideoRetryBudgetAuthorizationError("video retry authorization is locked") from exc
        except VideoRetryBudgetAuthorizationError:
            raise
        except OSError as exc:
            raise VideoRetryBudgetAuthorizationError(
                "video retry authorization could not be persisted"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            with suppress(OSError):
                lock.unlink()


__all__ = [
    "FilesystemVideoRetryBudgetAuthorizationStore",
    "VideoRetryBudgetAuthorizationError",
]


def _is_safely_retryable_http_status(status: int) -> bool:
    return status in {408, 409, 425, 429} or 500 <= status <= 599


def _attempt_key(path: Path) -> tuple[int, str]:
    attempt = next(
        (
            int(part.removeprefix("attempt-"))
            for part in path.parts
            if part.startswith("attempt-") and part.removeprefix("attempt-").isdigit()
        ),
        0,
    )
    return attempt, path.as_posix()
