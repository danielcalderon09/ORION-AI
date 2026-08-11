"""Metadata-driven ProductionVisualAssetPlan reader for ACQUIRING_ASSETS."""

import asyncio
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.binary_assets.exceptions import (
    BinaryAssetLinkError,
    BinaryAssetNotFoundError,
    BinaryAssetPathError,
)
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.domain.enums import ArtifactType
from backend.src.production.domain.path_rules import validate_relative_path
from backend.src.production.image_acquisition.exceptions import (
    ProductionVisualAssetPlanAmbiguousException,
    ProductionVisualAssetPlanChecksumException,
    ProductionVisualAssetPlanContractException,
    ProductionVisualAssetPlanEncodingException,
    ProductionVisualAssetPlanJobException,
    ProductionVisualAssetPlanJsonException,
    ProductionVisualAssetPlanLinkException,
    ProductionVisualAssetPlanMissingFileException,
    ProductionVisualAssetPlanNotFoundException,
    ProductionVisualAssetPlanPathException,
    ProductionVisualAssetPlanSizeException,
    ProductionVisualAssetPlanTransientReadException,
    ProductionVisualAssetPlanTypeException,
    ProductionVisualAssetPlanVersionException,
)
from backend.src.production.image_acquisition.ports import (
    ProductionVisualAssetPlanArtifactCandidate,
    ProductionVisualAssetPlanArtifactQueryRepository,
    ReadProductionVisualAssetPlan,
)
from backend.src.production.runtime.context import StageContext
from backend.src.production.visual_asset_planning.models import (
    SUPPORTED_VISUAL_ASSET_PLAN_VERSIONS,
    ProductionVisualAssetPlan,
)

_SOURCE_METADATA_ALLOWLIST = frozenset(
    {
        "schema_version",
        "source_scene_plan_schema_version",
        "source_scene_plan_artifact_id",
        "source_scene_plan_sha256",
        "source_shot_expansion_artifact_id",
        "source_shot_expansion_sha256",
        "source_shot_expansion_fingerprint",
        "visual_asset_planning_prompt_version",
        "asset_count",
        "scene_count",
        "shot_count",
        "recovered",
    }
)


class DurableProductionVisualAssetPlanReader:
    def __init__(
        self,
        *,
        workspace_root: Path,
        repository: ProductionVisualAssetPlanArtifactQueryRepository,
        max_plan_bytes: int,
    ) -> None:
        if max_plan_bytes < 1:
            raise ValueError("maximum visual asset plan size must be positive")
        self._confinement = WorkspaceConfinement(workspace_root)
        self._repository = repository
        self._max_bytes = max_plan_bytes

    async def read_for_image_acquisition(
        self,
        *,
        context: StageContext,
    ) -> ReadProductionVisualAssetPlan:
        return await asyncio.to_thread(self._read_sync, context)

    def _read_sync(self, context: StageContext) -> ReadProductionVisualAssetPlan:
        candidates = self._repository.list_candidates(job_id=context.job_id)
        selected = self._select(candidates, context)
        if selected.job_id != context.job_id:
            raise ProductionVisualAssetPlanJobException(
                "visual asset plan belongs to another job"
            )
        if selected.artifact_type is not ArtifactType.PRODUCTION_VISUAL_ASSET_PLAN:
            raise ProductionVisualAssetPlanTypeException(
                "input artifact is not a production visual asset plan"
            )
        self._validate_path(selected.relative_path, job_id=context.job_id)
        if selected.size_bytes is None or selected.sha256 is None:
            raise ProductionVisualAssetPlanContractException(
                "visual asset plan integrity metadata is missing"
            )
        target = self._target(selected.relative_path)
        content = self._read_bytes(target)
        if len(content) != selected.size_bytes:
            raise ProductionVisualAssetPlanSizeException(
                "visual asset plan size differs from durable metadata"
            )
        digest = hashlib.sha256(content).hexdigest()
        if digest.lower() != selected.sha256.lower():
            raise ProductionVisualAssetPlanChecksumException(
                "visual asset plan checksum differs from durable metadata"
            )
        try:
            decoded = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ProductionVisualAssetPlanEncodingException(
                "visual asset plan is not valid UTF-8"
            ) from exc
        try:
            payload = json.loads(
                decoded,
                parse_constant=_reject_constant,
                object_pairs_hook=_reject_duplicate_keys,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProductionVisualAssetPlanJsonException(
                "visual asset plan is not valid strict JSON"
            ) from exc
        try:
            plan = ProductionVisualAssetPlan.model_validate(payload)
        except (ValidationError, TypeError, ValueError) as exc:
            raise ProductionVisualAssetPlanContractException(
                "visual asset plan failed contract validation"
            ) from exc
        if plan.schema_version not in SUPPORTED_VISUAL_ASSET_PLAN_VERSIONS:
            raise ProductionVisualAssetPlanVersionException(
                "visual asset plan schema version is unsupported"
            )
        return ReadProductionVisualAssetPlan(
            visual_asset_plan=plan,
            job_id=selected.job_id,
            artifact_id=selected.artifact_id,
            relative_path=selected.relative_path,
            sha256=digest,
            size_bytes=len(content),
            schema_version=plan.schema_version,
            provider=selected.provider,
            model_version=selected.model_version,
            created_at=selected.created_at,
            metadata=_safe_metadata(selected.metadata),
        )

    def _select(
        self,
        candidates: tuple[ProductionVisualAssetPlanArtifactCandidate, ...],
        context: StageContext,
    ) -> ProductionVisualAssetPlanArtifactCandidate:
        by_id = {item.artifact_id: item for item in candidates}
        preferred = tuple(
            by_id[artifact_id]
            for artifact_id in context.input_artifact_ids
            if artifact_id in by_id
        )
        if len(preferred) > 1:
            raise ProductionVisualAssetPlanAmbiguousException(
                "multiple visual asset plan inputs were supplied"
            )
        if preferred:
            return preferred[0]
        input_types = self._repository.list_input_artifact_types(
            job_id=context.job_id,
            artifact_ids=context.input_artifact_ids,
        )
        if (
            input_types
            and ArtifactType.PRODUCTION_VISUAL_ASSET_PLAN
            not in input_types.values()
        ):
            raise ProductionVisualAssetPlanTypeException(
                "input artifacts do not contain a visual asset plan"
            )
        if not candidates:
            raise ProductionVisualAssetPlanNotFoundException(
                "no durable visual asset plan is registered"
            )
        return max(
            candidates,
            key=lambda item: (
                _attempt(item.relative_path),
                item.created_at,
                str(item.artifact_id),
            ),
        )

    @staticmethod
    def _validate_path(relative_path: str, *, job_id: UUID) -> None:
        try:
            normalized = validate_relative_path(relative_path)
        except ValueError as exc:
            raise ProductionVisualAssetPlanPathException(
                "visual asset plan path is unsafe"
            ) from exc
        parts = PurePosixPath(normalized).parts
        if (
            "\\" in normalized
            or len(parts) != 5
            or parts[0] != "production"
            or parts[1] != str(job_id)
            or parts[2] != "visual_asset_planning"
            or not parts[3].startswith("attempt-")
            or not parts[3][8:].isdigit()
            or int(parts[3][8:]) < 1
            or parts[4] != "visual-asset-plan.json"
        ):
            raise ProductionVisualAssetPlanPathException(
                "visual asset plan path is not contractual"
            )

    def _target(self, relative_path: str) -> Path:
        try:
            target = self._confinement.resolve(relative_path, require_exists=True)
            self._confinement.reject_unsafe_file(target)
            return target
        except BinaryAssetLinkError as exc:
            raise ProductionVisualAssetPlanLinkException(
                "visual asset plan path contains an unsafe link"
            ) from exc
        except BinaryAssetPathError as exc:
            raise ProductionVisualAssetPlanPathException(
                "visual asset plan path is unsafe"
            ) from exc
        except BinaryAssetNotFoundError as exc:
            raise ProductionVisualAssetPlanMissingFileException(
                "visual asset plan file is missing"
            ) from exc

    def _read_bytes(self, target: Path) -> bytes:
        try:
            if target.stat().st_size > self._max_bytes:
                raise ProductionVisualAssetPlanSizeException(
                    "visual asset plan exceeds the configured limit"
                )
            with target.open("rb") as stream:
                content = stream.read(self._max_bytes + 1)
        except ProductionVisualAssetPlanSizeException:
            raise
        except FileNotFoundError as exc:
            raise ProductionVisualAssetPlanMissingFileException(
                "visual asset plan file is missing"
            ) from exc
        except (BlockingIOError, PermissionError, OSError) as exc:
            raise ProductionVisualAssetPlanTransientReadException(
                "visual asset plan could not be read temporarily"
            ) from exc
        if len(content) > self._max_bytes:
            raise ProductionVisualAssetPlanSizeException(
                "visual asset plan exceeds the configured limit"
            )
        return content


def _attempt(relative_path: str) -> int:
    parts = PurePosixPath(relative_path).parts
    if len(parts) == 5 and parts[3].startswith("attempt-"):
        value = parts[3][8:]
        return int(value) if value.isdigit() else -1
    return -1


def _safe_metadata(value: dict[str, Any]) -> dict[str, Any]:
    filtered = {
        key: value[key] for key in sorted(value) if key in _SOURCE_METADATA_ALLOWLIST
    }
    result = validate_safe_json(filtered, path="source_visual_asset_plan.metadata")
    if not isinstance(result, dict):
        raise ProductionVisualAssetPlanContractException(
            "visual asset plan source metadata is invalid"
        )
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result
