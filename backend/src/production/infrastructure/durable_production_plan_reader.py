"""Filesystem-backed, metadata-driven ProductionPlan reader."""

import asyncio
import hashlib
import json
from pathlib import Path, PurePosixPath
from uuid import UUID

from pydantic import ValidationError

from backend.src.production.domain.path_rules import validate_relative_path
from backend.src.production.planning.models import ProductionPlan
from backend.src.production.runtime.context import StageContext
from backend.src.production.scripting.exceptions import (
    ProductionPlanChecksumError,
    ProductionPlanContractError,
    ProductionPlanEncodingError,
    ProductionPlanIntegrityError,
    ProductionPlanJsonError,
    ProductionPlanMissingFileError,
    ProductionPlanNotFoundError,
    ProductionPlanPathError,
    ProductionPlanSizeError,
    ProductionPlanTransientReadError,
    ProductionPlanVersionError,
)
from backend.src.production.scripting.ports import (
    ProductionPlanArtifactCandidate,
    ProductionPlanArtifactQueryRepository,
    ReadProductionPlan,
)

SUPPORTED_PRODUCTION_PLAN_VERSIONS = frozenset({"1.0.0"})


class DurableProductionPlanReader:
    """Deliver a plan only after its durable record and bytes agree."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        repository: ProductionPlanArtifactQueryRepository,
        max_plan_bytes: int,
    ) -> None:
        if max_plan_bytes < 1:
            raise ValueError("maximum production plan size must be positive")
        expanded = workspace_root.expanduser()
        if expanded.is_symlink():
            raise ValueError("production workspace root cannot be a symbolic link")
        self._root = expanded.resolve()
        self._repository = repository
        self._max_bytes = max_plan_bytes

    async def read_for_scripting(self, *, context: StageContext) -> ReadProductionPlan:
        return await asyncio.to_thread(self._read_sync, context)

    def _read_sync(self, context: StageContext) -> ReadProductionPlan:
        candidates = self._repository.list_candidates(job_id=context.job_id)
        selected = self._select(candidates, context.input_artifact_ids)
        if selected.job_id != context.job_id:
            raise ProductionPlanIntegrityError("production plan belongs to another job")
        attempt = self._validate_contractual_path(
            selected.relative_path,
            job_id=context.job_id,
        )
        if attempt < 1:  # pragma: no cover - guarded by path parsing
            raise ProductionPlanPathError("production plan attempt is invalid")
        if selected.size_bytes is None or selected.sha256 is None:
            raise ProductionPlanIntegrityError("production plan integrity metadata is missing")
        target = self._resolve_safe_target(selected.relative_path)
        content = self._read_bytes(target)
        if len(content) != selected.size_bytes:
            raise ProductionPlanSizeError("production plan size does not match durable metadata")
        digest = hashlib.sha256(content).hexdigest()
        if digest.lower() != selected.sha256.lower():
            raise ProductionPlanChecksumError(
                "production plan checksum does not match durable metadata"
            )
        try:
            decoded = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ProductionPlanEncodingError("production plan is not valid UTF-8") from exc
        try:
            payload = json.loads(
                decoded,
                parse_constant=_reject_json_constant,
                object_pairs_hook=_reject_duplicate_json_keys,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProductionPlanJsonError("production plan is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ProductionPlanJsonError("production plan JSON must be an object")
        try:
            plan = ProductionPlan.model_validate(payload)
        except (ValidationError, TypeError, ValueError) as exc:
            raise ProductionPlanContractError(
                "production plan failed contract validation"
            ) from exc
        if plan.schema_version not in SUPPORTED_PRODUCTION_PLAN_VERSIONS:
            raise ProductionPlanVersionError("production plan schema version is unsupported")
        return ReadProductionPlan(
            plan=plan,
            artifact_id=selected.artifact_id,
            relative_path=selected.relative_path,
            sha256=digest,
            size_bytes=len(content),
            schema_version=plan.schema_version,
            provider=selected.provider,
            model_version=selected.model_version,
            created_at=selected.created_at,
            metadata=_safe_source_metadata(selected.metadata),
        )

    @staticmethod
    def _select(
        candidates: tuple[ProductionPlanArtifactCandidate, ...],
        preferred_ids: tuple[UUID, ...],
    ) -> ProductionPlanArtifactCandidate:
        if not candidates:
            raise ProductionPlanNotFoundError("no durable production plan is registered")
        by_id = {candidate.artifact_id: candidate for candidate in candidates}
        preferred = tuple(by_id[item] for item in preferred_ids if item in by_id)
        pool = preferred or candidates

        def key(candidate: ProductionPlanArtifactCandidate) -> tuple[int, object, str]:
            attempt = DurableProductionPlanReader._attempt_or_negative(
                candidate.relative_path
            )
            return attempt, candidate.created_at, str(candidate.artifact_id)

        return max(pool, key=key)

    @staticmethod
    def _attempt_or_negative(relative_path: str) -> int:
        parts = PurePosixPath(relative_path).parts
        if len(parts) == 5 and parts[3].startswith("attempt-"):
            value = parts[3][8:]
            if value.isdigit():
                return int(value)
        return -1

    @staticmethod
    def _validate_contractual_path(relative_path: str, *, job_id: UUID) -> int:
        try:
            normalized = validate_relative_path(relative_path)
        except ValueError as exc:
            raise ProductionPlanPathError("production plan path is unsafe") from exc
        if "\\" in normalized:
            raise ProductionPlanPathError("production plan path must use POSIX separators")
        parts = PurePosixPath(normalized).parts
        if (
            len(parts) != 5
            or parts[0] != "production"
            or parts[1] != str(job_id)
            or parts[2] != "planning"
            or parts[4] != "production-plan.json"
            or not parts[3].startswith("attempt-")
            or not parts[3][8:].isdigit()
            or int(parts[3][8:]) < 1
        ):
            raise ProductionPlanPathError("production plan path is not contractual for this job")
        return int(parts[3][8:])

    def _resolve_safe_target(self, relative_path: str) -> Path:
        target = self._root.joinpath(*PurePosixPath(relative_path).parts)
        try:
            target.resolve(strict=False).relative_to(self._root)
        except ValueError as exc:
            raise ProductionPlanPathError("production plan escaped workspace root") from exc
        current = self._root
        for part in target.relative_to(self._root).parts:
            current /= part
            if current.is_symlink():
                raise ProductionPlanPathError("production plan path contains a symbolic link")
        if not target.exists():
            raise ProductionPlanMissingFileError("production plan file is missing")
        if not target.is_file():
            raise ProductionPlanPathError("production plan target is not a regular file")
        return target

    def _read_bytes(self, target: Path) -> bytes:
        try:
            stat_size = target.stat().st_size
            if stat_size > self._max_bytes:
                raise ProductionPlanSizeError("production plan exceeds the configured limit")
            with target.open("rb") as stream:
                content = stream.read(self._max_bytes + 1)
            if len(content) > self._max_bytes:
                raise ProductionPlanSizeError("production plan exceeds the configured limit")
            return content
        except ProductionPlanSizeError:
            raise
        except (BlockingIOError, PermissionError) as exc:
            raise ProductionPlanTransientReadError(
                "production plan could not be read temporarily"
            ) from exc
        except FileNotFoundError as exc:
            raise ProductionPlanMissingFileError("production plan file is missing") from exc
        except OSError as exc:
            raise ProductionPlanTransientReadError(
                "production plan could not be read temporarily"
            ) from exc


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result


def _safe_source_metadata(metadata: dict[str, object]) -> dict[str, object]:
    allowed = frozenset(
        {
            "schema_version",
            "prompt_version",
            "scene_count",
            "latency_ms",
            "request_id",
            "requested_model",
            "reported_model",
            "model_mismatch",
            "simulated",
            "deterministic",
        }
    )
    return {key: value for key, value in metadata.items() if key in allowed}
