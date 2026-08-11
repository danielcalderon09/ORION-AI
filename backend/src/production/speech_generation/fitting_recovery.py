"""Explicit durable authorization for historical narration-fitting recovery."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.speech_generation.models import (
    SpeechGenerationManifest,
)
from backend.src.production.speech_generation.narration_fitting import (
    NarrationFittingStatus,
)
from backend.src.production.speech_generation.serialization import (
    deserialize_speech_manifest,
    serialize_speech_manifest,
)


class NarrationFittingRecoveryAuthorizationError(RuntimeError):
    """Safe fail-closed recovery authorization failure."""


class NarrationFittingRecoveryAuthorization(ContractModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    job_id: UUID
    stage: Literal[ProductionStage.GENERATING_NARRATION] = (
        ProductionStage.GENERATING_NARRATION
    )
    source_attempt_number: int = Field(ge=1)
    previous_authorized_job_cost_usd: Decimal = Field(gt=0)
    new_authorized_job_cost_usd: Decimal = Field(gt=0)
    additional_authorized_cost_usd: Decimal = Field(gt=0)
    existing_committed_estimate_usd: Decimal = Field(ge=0)
    estimated_cost_per_provider_request_usd: Decimal = Field(gt=0)
    maximum_additional_fitting_attempts: Literal[1] = 1
    maximum_additional_provider_requests: int = Field(ge=1, le=2)
    reason: Literal["operator_authorized_failed_fitting_recovery"] = (
        "operator_authorized_failed_fitting_recovery"
    )
    source_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator(
        "previous_authorized_job_cost_usd",
        "new_authorized_job_cost_usd",
        "additional_authorized_cost_usd",
        "existing_committed_estimate_usd",
        "estimated_cost_per_provider_request_usd",
        mode="before",
    )
    @classmethod
    def reject_float_money(cls, value: object) -> object:
        if isinstance(value, float):
            raise ValueError("recovery authorization money must not use float")
        return value

    @field_validator("created_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recovery authorization timestamp must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_authorization(self) -> NarrationFittingRecoveryAuthorization:
        if self.new_authorized_job_cost_usd <= self.previous_authorized_job_cost_usd:
            raise ValueError("recovery authorization must increase the historical budget")
        if self.new_authorized_job_cost_usd < self.existing_committed_estimate_usd:
            raise ValueError("recovery authorization is below committed estimated exposure")
        if (
            self.additional_authorized_cost_usd
            != self.new_authorized_job_cost_usd - self.existing_committed_estimate_usd
        ):
            raise ValueError("recovery additional authorization is inconsistent")
        capacity = int(
            (
                self.additional_authorized_cost_usd
                / self.estimated_cost_per_provider_request_usd
            ).to_integral_value(rounding=ROUND_FLOOR)
        )
        if capacity != self.maximum_additional_provider_requests:
            raise ValueError("recovery provider request capacity is inconsistent")
        expected = narration_fitting_recovery_authorization_fingerprint(self)
        if self.fingerprint != expected:
            raise ValueError("recovery authorization fingerprint differs")
        return self


def narration_fitting_recovery_authorization_fingerprint(
    authorization: NarrationFittingRecoveryAuthorization,
) -> str:
    payload = authorization.model_dump(
        mode="json",
        exclude={"fingerprint"},
    )
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def speech_manifest_sha256(manifest: SpeechGenerationManifest) -> str:
    return hashlib.sha256(serialize_speech_manifest(manifest)).hexdigest()


class FilesystemNarrationFittingRecoveryAuthorizationStore:
    def __init__(self, workspace_root: Path, *, maximum_bytes: int = 64_000) -> None:
        if not 1_024 <= maximum_bytes <= 1_000_000:
            raise ValueError("recovery authorization size limit is invalid")
        self._confinement = WorkspaceConfinement(workspace_root)
        self._maximum = maximum_bytes

    async def read(
        self,
        *,
        job_id: UUID,
    ) -> NarrationFittingRecoveryAuthorization | None:
        return await asyncio.to_thread(self._read_sync, job_id)

    async def authorize(
        self,
        *,
        job_id: UUID,
        job_status: ProductionJobStatus,
        current_stage: ProductionStage,
        new_authorized_job_cost_usd: Decimal,
        current_settings_authorization_usd: Decimal,
        estimated_cost_per_provider_request_usd: Decimal,
        maximum_fitting_attempts: int,
        maximum_provider_retries: int,
        clock: Callable[[], datetime],
    ) -> tuple[NarrationFittingRecoveryAuthorization, bool]:
        return await asyncio.to_thread(
            self._authorize_sync,
            job_id,
            job_status,
            current_stage,
            new_authorized_job_cost_usd,
            current_settings_authorization_usd,
            estimated_cost_per_provider_request_usd,
            maximum_fitting_attempts,
            maximum_provider_retries,
            clock,
        )

    async def load_source_manifest(
        self,
        authorization: NarrationFittingRecoveryAuthorization,
    ) -> SpeechGenerationManifest:
        return await asyncio.to_thread(self._load_source_manifest_sync, authorization)

    def _authorize_sync(
        self,
        job_id: UUID,
        job_status: ProductionJobStatus,
        current_stage: ProductionStage,
        new_budget: Decimal,
        settings_budget: Decimal,
        estimated_per_request: Decimal,
        maximum_fitting_attempts: int,
        maximum_provider_retries: int,
        clock: Callable[[], datetime],
    ) -> tuple[NarrationFittingRecoveryAuthorization, bool]:
        if job_status not in {
            ProductionJobStatus.FAILED,
            ProductionJobStatus.NEEDS_USER_ACTION,
        } or current_stage is not ProductionStage.GENERATING_NARRATION:
            raise NarrationFittingRecoveryAuthorizationError(
                "job is not in narration recovery state"
            )
        if new_budget > settings_budget:
            raise NarrationFittingRecoveryAuthorizationError(
                "requested recovery budget exceeds current Settings authorization"
            )
        if not 0 <= maximum_provider_retries <= 1:
            raise NarrationFittingRecoveryAuthorizationError(
                "provider retry policy is outside safe limits"
            )
        if not 1 <= maximum_fitting_attempts <= 5:
            raise NarrationFittingRecoveryAuthorizationError(
                "fitting attempt policy is outside safe limits"
            )
        source = self._latest_failed_manifest(job_id)
        committed = sum(
            (
                record.estimated_cost_usd * (1 + record.provider_retry_count)
                for record in source.fitting_records
            ),
            Decimal(0),
        )
        historical = max(
            committed,
            *(record.maximum_authorized_cost_usd for record in source.fitting_records),
        )
        compatible_failures = tuple(
            record
            for record in source.fitting_records
            if record.status is NarrationFittingStatus.FAILED
            and record.safe_error_code
            in {
                "provider_error",
                "timeout",
                "connect_error",
                "http_429",
                "http_5xx",
            }
        )
        if not compatible_failures:
            raise NarrationFittingRecoveryAuthorizationError(
                "manifest has no compatible failed fitting record"
            )
        if max(record.attempt_number for record in source.fitting_records) >= (
            maximum_fitting_attempts
        ):
            raise NarrationFittingRecoveryAuthorizationError(
                "current fitting attempt policy is exhausted"
            )
        if new_budget <= historical:
            raise NarrationFittingRecoveryAuthorizationError(
                "recovery budget must exceed historical authorization"
            )
        additional = new_budget - committed
        if additional <= 0:
            raise NarrationFittingRecoveryAuthorizationError(
                "recovery budget does not add available exposure"
            )
        capacity = int(
            (additional / estimated_per_request).to_integral_value(rounding=ROUND_FLOOR)
        )
        maximum_capacity = 1 + maximum_provider_retries
        if capacity < 1 or capacity > maximum_capacity:
            raise NarrationFittingRecoveryAuthorizationError(
                "recovery request capacity does not match current policy"
            )
        source_sha = speech_manifest_sha256(source)
        target = self._authorization_target(job_id)
        existing = self._read_target(target)
        if existing is not None:
            if (
                existing.source_attempt_number == source.attempt_number
                and existing.previous_authorized_job_cost_usd == historical
                and existing.new_authorized_job_cost_usd == new_budget
                and existing.additional_authorized_cost_usd == additional
                and existing.existing_committed_estimate_usd == committed
                and existing.estimated_cost_per_provider_request_usd
                == estimated_per_request
                and existing.maximum_additional_provider_requests == capacity
                and existing.source_manifest_sha256 == source_sha
            ):
                self._verify_source(existing)
                return existing, True
            raise NarrationFittingRecoveryAuthorizationError(
                "a different recovery authorization already exists"
            )
        created_at = clock()
        candidate = NarrationFittingRecoveryAuthorization.model_construct(
            job_id=job_id,
            source_attempt_number=source.attempt_number,
            previous_authorized_job_cost_usd=historical,
            new_authorized_job_cost_usd=new_budget,
            additional_authorized_cost_usd=additional,
            existing_committed_estimate_usd=committed,
            estimated_cost_per_provider_request_usd=estimated_per_request,
            maximum_additional_provider_requests=capacity,
            source_manifest_sha256=source_sha,
            created_at=created_at,
            fingerprint="0" * 64,
        )
        candidate = NarrationFittingRecoveryAuthorization.model_validate(
            candidate.model_copy(
                update={
                    "fingerprint": narration_fitting_recovery_authorization_fingerprint(
                        candidate
                    )
                }
            )
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        self._confinement.reject_unsafe_components(target)
        self._atomic_create(target, _serialize_authorization(candidate))
        return candidate, False

    def _latest_failed_manifest(self, job_id: UUID) -> SpeechGenerationManifest:
        base = self._confinement.resolve(
            f"production/{job_id}/generating_narration"
        )
        manifests: list[SpeechGenerationManifest] = []
        if base.exists():
            for target in sorted(base.glob("attempt-*/speech-generation-manifest.json")):
                try:
                    self._confinement.reject_unsafe_file(target)
                    content = target.read_bytes()
                    if len(content) > 16_000_000:
                        continue
                    manifest = deserialize_speech_manifest(content)
                except (OSError, ValueError):
                    continue
                if manifest.job_id == job_id and any(
                    record.status is NarrationFittingStatus.FAILED
                    for record in manifest.fitting_records
                ):
                    manifests.append(manifest)
        if not manifests:
            raise NarrationFittingRecoveryAuthorizationError(
                "no failed narration manifest was found"
            )
        return max(manifests, key=lambda item: item.attempt_number)

    def _load_source_manifest_sync(
        self,
        authorization: NarrationFittingRecoveryAuthorization,
    ) -> SpeechGenerationManifest:
        target = self._confinement.resolve(
            "production/"
            f"{authorization.job_id}/generating_narration/"
            f"attempt-{authorization.source_attempt_number}/speech-generation-manifest.json"
        )
        self._confinement.reject_unsafe_file(target)
        content = target.read_bytes()
        if len(content) > 16_000_000:
            raise NarrationFittingRecoveryAuthorizationError(
                "source speech manifest exceeds safe size"
            )
        manifest = deserialize_speech_manifest(content)
        if speech_manifest_sha256(manifest) != authorization.source_manifest_sha256:
            raise NarrationFittingRecoveryAuthorizationError(
                "source speech manifest fingerprint drifted"
            )
        return manifest

    def _verify_source(self, authorization: NarrationFittingRecoveryAuthorization) -> None:
        self._load_source_manifest_sync(authorization)

    def _read_sync(
        self,
        job_id: UUID,
    ) -> NarrationFittingRecoveryAuthorization | None:
        authorization = self._read_target(self._authorization_target(job_id))
        if authorization is not None:
            self._verify_source(authorization)
        return authorization

    def _read_target(
        self,
        target: Path,
    ) -> NarrationFittingRecoveryAuthorization | None:
        if not target.exists():
            return None
        self._confinement.reject_unsafe_file(target)
        content = target.read_bytes()
        if len(content) > self._maximum:
            raise NarrationFittingRecoveryAuthorizationError(
                "recovery authorization exceeds safe size"
            )
        try:
            payload = json.loads(content.decode("utf-8"))
            return NarrationFittingRecoveryAuthorization.model_validate(payload)
        except (UnicodeError, ValueError, TypeError) as exc:
            raise NarrationFittingRecoveryAuthorizationError(
                "recovery authorization is invalid"
            ) from exc

    def _authorization_target(self, job_id: UUID) -> Path:
        return self._confinement.resolve(
            "production/"
            f"{job_id}/generating_narration/"
            "narration-fitting-recovery-authorization.json"
        )

    def _atomic_create(self, target: Path, content: bytes) -> None:
        if len(content) > self._maximum:
            raise NarrationFittingRecoveryAuthorizationError(
                "recovery authorization exceeds safe size"
            )
        descriptor = -1
        lock_descriptor = -1
        temporary: Path | None = None
        lock = target.with_name(f".{target.name}.lock")
        try:
            lock_descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            descriptor, name = tempfile.mkstemp(
                prefix=".fitting-recovery-",
                suffix=".tmp",
                dir=target.parent,
            )
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if target.exists():
                raise NarrationFittingRecoveryAuthorizationError(
                    "recovery authorization changed concurrently"
                )
            os.replace(temporary, target)
            temporary = None
            self._confinement.reject_unsafe_file(target)
        except FileExistsError as exc:
            raise NarrationFittingRecoveryAuthorizationError(
                "recovery authorization changed concurrently"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if lock_descriptor >= 0:
                os.close(lock_descriptor)
                lock.unlink(missing_ok=True)
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _serialize_authorization(
    authorization: NarrationFittingRecoveryAuthorization,
) -> bytes:
    return (
        json.dumps(
            authorization.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


__all__ = [
    "FilesystemNarrationFittingRecoveryAuthorizationStore",
    "NarrationFittingRecoveryAuthorization",
    "NarrationFittingRecoveryAuthorizationError",
    "narration_fitting_recovery_authorization_fingerprint",
    "speech_manifest_sha256",
]
