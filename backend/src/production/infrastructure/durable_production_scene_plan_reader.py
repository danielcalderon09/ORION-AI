"""Metadata-driven ProductionScenePlan reader for VISUAL_ASSET_PLANNING."""

import asyncio
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from backend.src.production.application.sanitization import (
    UnsafeProductionDataError,
    validate_safe_json,
)
from backend.src.production.domain.enums import ArtifactType
from backend.src.production.domain.path_rules import validate_relative_path
from backend.src.production.runtime.context import StageContext
from backend.src.production.scene_planning.models import ProductionScenePlan
from backend.src.production.visual_asset_planning.exceptions import (
    ProductionScenePlanAmbiguousException,
    ProductionScenePlanChecksumException,
    ProductionScenePlanContractException,
    ProductionScenePlanEncodingException,
    ProductionScenePlanIntegrityException,
    ProductionScenePlanJsonException,
    ProductionScenePlanMissingFileException,
    ProductionScenePlanNotFoundException,
    ProductionScenePlanPathException,
    ProductionScenePlanSizeException,
    ProductionScenePlanSymlinkException,
    ProductionScenePlanTransientReadException,
    ProductionScenePlanTypeException,
    ProductionScenePlanVersionException,
)
from backend.src.production.visual_asset_planning.ports import (
    ProductionScenePlanArtifactCandidate,
    ProductionScenePlanArtifactQueryRepository,
    ReadProductionScenePlan,
)

SUPPORTED_PRODUCTION_SCENE_PLAN_VERSIONS = frozenset({"1.0.0"})
_SOURCE_METADATA_ALLOWLIST = frozenset(
    {
        "schema_version",
        "source_script_schema_version",
        "source_script_artifact_id",
        "source_script_sha256",
        "scene_planning_prompt_version",
        "scene_count",
        "shot_count",
        "recovered",
    }
)


class DurableProductionScenePlanReader:
    def __init__(
        self,
        *,
        workspace_root: Path,
        repository: ProductionScenePlanArtifactQueryRepository,
        max_scene_plan_bytes: int,
    ) -> None:
        if max_scene_plan_bytes < 1:
            raise ValueError("maximum production scene plan size must be positive")
        expanded = workspace_root.expanduser()
        if expanded.is_symlink():
            raise ValueError("production workspace root cannot be a symbolic link")
        self._root = expanded.resolve()
        self._repository = repository
        self._max_bytes = max_scene_plan_bytes

    async def read_for_visual_asset_planning(
        self,
        *,
        context: StageContext,
    ) -> ReadProductionScenePlan:
        return await asyncio.to_thread(self._read_sync, context)

    def _read_sync(self, context: StageContext) -> ReadProductionScenePlan:
        candidates = self._repository.list_candidates(job_id=context.job_id)
        selected = self._select(candidates, context)
        if selected.job_id != context.job_id:
            raise ProductionScenePlanIntegrityException(
                "production scene plan belongs to another job"
            )
        if selected.artifact_type is not ArtifactType.PRODUCTION_SCENE_PLAN:
            raise ProductionScenePlanTypeException("input artifact is not a production scene plan")
        self._validate_contractual_path(selected.relative_path, job_id=context.job_id)
        if selected.size_bytes is None or selected.sha256 is None:
            raise ProductionScenePlanIntegrityException(
                "production scene plan integrity metadata is missing"
            )
        target = self._resolve_safe_target(selected.relative_path)
        content = self._read_bytes(target)
        if len(content) != selected.size_bytes:
            raise ProductionScenePlanSizeException(
                "production scene plan size does not match durable metadata"
            )
        digest = hashlib.sha256(content).hexdigest()
        if digest.lower() != selected.sha256.lower():
            raise ProductionScenePlanChecksumException(
                "production scene plan checksum does not match durable metadata"
            )
        try:
            decoded = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ProductionScenePlanEncodingException(
                "production scene plan is not valid UTF-8"
            ) from exc
        try:
            payload = json.loads(
                decoded,
                parse_constant=_reject_json_constant,
                object_pairs_hook=_reject_duplicate_json_keys,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProductionScenePlanJsonException(
                "production scene plan is not valid strict JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise ProductionScenePlanJsonException("production scene plan JSON must be an object")
        try:
            scene_plan = ProductionScenePlan.model_validate(payload)
        except (ValidationError, TypeError, ValueError) as exc:
            raise ProductionScenePlanContractException(
                "production scene plan failed contract validation"
            ) from exc
        if scene_plan.schema_version not in SUPPORTED_PRODUCTION_SCENE_PLAN_VERSIONS:
            raise ProductionScenePlanVersionException(
                "production scene plan schema version is unsupported"
            )
        return ReadProductionScenePlan(
            scene_plan=scene_plan,
            artifact_id=selected.artifact_id,
            relative_path=selected.relative_path,
            sha256=digest,
            size_bytes=len(content),
            schema_version=scene_plan.schema_version,
            provider=selected.provider,
            model_version=selected.model_version,
            created_at=selected.created_at,
            metadata=_safe_source_metadata(selected.metadata),
        )

    def _select(
        self,
        candidates: tuple[ProductionScenePlanArtifactCandidate, ...],
        context: StageContext,
    ) -> ProductionScenePlanArtifactCandidate:
        by_id = {candidate.artifact_id: candidate for candidate in candidates}
        preferred = tuple(by_id[item] for item in context.input_artifact_ids if item in by_id)
        if len(preferred) > 1:
            raise ProductionScenePlanAmbiguousException(
                "multiple production scene plan inputs were supplied"
            )
        if len(preferred) == 1:
            return preferred[0]
        input_types = self._repository.list_input_artifact_types(
            job_id=context.job_id,
            artifact_ids=context.input_artifact_ids,
        )
        if input_types and ArtifactType.PRODUCTION_SCENE_PLAN not in input_types.values():
            raise ProductionScenePlanTypeException(
                "input artifacts do not contain a production scene plan"
            )
        if not candidates:
            raise ProductionScenePlanNotFoundException(
                "no durable production scene plan is registered"
            )
        return max(
            candidates,
            key=lambda candidate: (
                self._attempt_or_negative(candidate.relative_path),
                candidate.created_at,
                str(candidate.artifact_id),
            ),
        )

    @staticmethod
    def _attempt_or_negative(relative_path: str) -> int:
        parts = PurePosixPath(relative_path).parts
        if len(parts) == 5 and parts[3].startswith("attempt-"):
            value = parts[3][8:]
            if value.isdigit():
                return int(value)
        return -1

    @staticmethod
    def _validate_contractual_path(relative_path: str, *, job_id: UUID) -> None:
        try:
            normalized = validate_relative_path(relative_path)
        except ValueError as exc:
            raise ProductionScenePlanPathException("production scene plan path is unsafe") from exc
        parts = PurePosixPath(normalized).parts
        if (
            "\\" in normalized
            or len(parts) != 5
            or parts[0] != "production"
            or parts[1] != str(job_id)
            or parts[2] != "scene_planning"
            or parts[4] != "scene-plan.json"
            or not parts[3].startswith("attempt-")
            or not parts[3][8:].isdigit()
            or int(parts[3][8:]) < 1
        ):
            raise ProductionScenePlanPathException(
                "production scene plan path is not contractual for this job"
            )

    def _resolve_safe_target(self, relative_path: str) -> Path:
        target = self._root.joinpath(*PurePosixPath(relative_path).parts)
        try:
            target.resolve(strict=False).relative_to(self._root)
        except ValueError as exc:
            raise ProductionScenePlanPathException(
                "production scene plan escaped workspace root"
            ) from exc
        current = self._root
        for part in target.relative_to(self._root).parts:
            current /= part
            if current.is_symlink():
                raise ProductionScenePlanSymlinkException(
                    "production scene plan path contains a symbolic link"
                )
        if not target.exists():
            raise ProductionScenePlanMissingFileException("production scene plan file is missing")
        if not target.is_file():
            raise ProductionScenePlanPathException(
                "production scene plan target is not a regular file"
            )
        return target

    def _read_bytes(self, target: Path) -> bytes:
        try:
            if target.stat().st_size > self._max_bytes:
                raise ProductionScenePlanSizeException(
                    "production scene plan exceeds the configured limit"
                )
            with target.open("rb") as stream:
                content = stream.read(self._max_bytes + 1)
            if len(content) > self._max_bytes:
                raise ProductionScenePlanSizeException(
                    "production scene plan exceeds the configured limit"
                )
            return content
        except ProductionScenePlanSizeException:
            raise
        except (BlockingIOError, PermissionError) as exc:
            raise ProductionScenePlanTransientReadException(
                "production scene plan could not be read temporarily"
            ) from exc
        except FileNotFoundError as exc:
            raise ProductionScenePlanMissingFileException(
                "production scene plan file is missing"
            ) from exc
        except OSError as exc:
            raise ProductionScenePlanTransientReadException(
                "production scene plan could not be read temporarily"
            ) from exc


def _safe_source_metadata(value: dict[str, Any]) -> dict[str, Any]:
    filtered = {key: value[key] for key in sorted(value) if key in _SOURCE_METADATA_ALLOWLIST}
    try:
        validated = validate_safe_json(filtered, path="source_scene_plan.metadata")
    except UnsafeProductionDataError as exc:
        raise ProductionScenePlanIntegrityException(
            "production scene plan source metadata is unsafe"
        ) from exc
    if not isinstance(validated, dict):
        raise ProductionScenePlanIntegrityException(
            "production scene plan source metadata is invalid"
        )
    return validated


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result
