"""Metadata-driven ProductionScript reader for SCENE_PLANNING."""

import asyncio
import hashlib
import json
from pathlib import Path, PurePosixPath
from uuid import UUID

from pydantic import ValidationError

from backend.src.production.domain.path_rules import validate_relative_path
from backend.src.production.runtime.context import StageContext
from backend.src.production.scene_planning.exceptions import (
    ProductionScriptChecksumException,
    ProductionScriptContractException,
    ProductionScriptEncodingException,
    ProductionScriptIntegrityException,
    ProductionScriptJsonException,
    ProductionScriptMissingFileException,
    ProductionScriptNotFoundException,
    ProductionScriptPathException,
    ProductionScriptSizeException,
    ProductionScriptTransientReadException,
    ProductionScriptVersionException,
)
from backend.src.production.scene_planning.ports import (
    ProductionScriptArtifactCandidate,
    ProductionScriptArtifactQueryRepository,
    ReadProductionScript,
)
from backend.src.production.scripting.models import ProductionScript

SUPPORTED_PRODUCTION_SCRIPT_VERSIONS = frozenset({"1.0.0"})


class DurableProductionScriptReader:
    def __init__(
        self,
        *,
        workspace_root: Path,
        repository: ProductionScriptArtifactQueryRepository,
        max_script_bytes: int,
    ) -> None:
        if max_script_bytes < 1:
            raise ValueError("maximum production script size must be positive")
        expanded = workspace_root.expanduser()
        if expanded.is_symlink():
            raise ValueError("production workspace root cannot be a symbolic link")
        self._root = expanded.resolve()
        self._repository = repository
        self._max_bytes = max_script_bytes

    async def read_for_scene_planning(
        self,
        *,
        context: StageContext,
    ) -> ReadProductionScript:
        return await asyncio.to_thread(self._read_sync, context)

    def _read_sync(self, context: StageContext) -> ReadProductionScript:
        selected = self._select(
            self._repository.list_candidates(job_id=context.job_id),
            context.input_artifact_ids,
        )
        if selected.job_id != context.job_id:
            raise ProductionScriptIntegrityException(
                "production script belongs to another job"
            )
        self._validate_contractual_path(selected.relative_path, job_id=context.job_id)
        if selected.size_bytes is None or selected.sha256 is None:
            raise ProductionScriptIntegrityException(
                "production script integrity metadata is missing"
            )
        target = self._resolve_safe_target(selected.relative_path)
        content = self._read_bytes(target)
        if len(content) != selected.size_bytes:
            raise ProductionScriptSizeException(
                "production script size does not match durable metadata"
            )
        digest = hashlib.sha256(content).hexdigest()
        if digest.lower() != selected.sha256.lower():
            raise ProductionScriptChecksumException(
                "production script checksum does not match durable metadata"
            )
        try:
            decoded = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ProductionScriptEncodingException(
                "production script is not valid UTF-8"
            ) from exc
        try:
            payload = json.loads(
                decoded,
                parse_constant=_reject_json_constant,
                object_pairs_hook=_reject_duplicate_json_keys,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProductionScriptJsonException(
                "production script is not valid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise ProductionScriptJsonException(
                "production script JSON must be an object"
            )
        try:
            script = ProductionScript.model_validate(payload)
        except (ValidationError, TypeError, ValueError) as exc:
            raise ProductionScriptContractException(
                "production script failed contract validation"
            ) from exc
        if script.schema_version not in SUPPORTED_PRODUCTION_SCRIPT_VERSIONS:
            raise ProductionScriptVersionException(
                "production script schema version is unsupported"
            )
        return ReadProductionScript(
            script=script,
            artifact_id=selected.artifact_id,
            relative_path=selected.relative_path,
            sha256=digest,
            size_bytes=len(content),
            schema_version=script.schema_version,
            provider=selected.provider,
            model_version=selected.model_version,
            created_at=selected.created_at,
        )

    @staticmethod
    def _select(
        candidates: tuple[ProductionScriptArtifactCandidate, ...],
        preferred_ids: tuple[UUID, ...],
    ) -> ProductionScriptArtifactCandidate:
        if not candidates:
            raise ProductionScriptNotFoundException(
                "no durable production script is registered"
            )
        by_id = {candidate.artifact_id: candidate for candidate in candidates}
        preferred = tuple(by_id[item] for item in preferred_ids if item in by_id)
        pool = preferred or candidates
        return max(
            pool,
            key=lambda candidate: (
                DurableProductionScriptReader._attempt_or_negative(
                    candidate.relative_path
                ),
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
            raise ProductionScriptPathException(
                "production script path is unsafe"
            ) from exc
        parts = PurePosixPath(normalized).parts
        if (
            "\\" in normalized
            or len(parts) != 5
            or parts[0] != "production"
            or parts[1] != str(job_id)
            or parts[2] != "scripting"
            or parts[4] != "production-script.json"
            or not parts[3].startswith("attempt-")
            or not parts[3][8:].isdigit()
            or int(parts[3][8:]) < 1
        ):
            raise ProductionScriptPathException(
                "production script path is not contractual for this job"
            )

    def _resolve_safe_target(self, relative_path: str) -> Path:
        target = self._root.joinpath(*PurePosixPath(relative_path).parts)
        try:
            target.resolve(strict=False).relative_to(self._root)
        except ValueError as exc:
            raise ProductionScriptPathException(
                "production script escaped workspace root"
            ) from exc
        current = self._root
        for part in target.relative_to(self._root).parts:
            current /= part
            if current.is_symlink():
                raise ProductionScriptPathException(
                    "production script path contains a symbolic link"
                )
        if not target.exists():
            raise ProductionScriptMissingFileException(
                "production script file is missing"
            )
        if not target.is_file():
            raise ProductionScriptPathException(
                "production script target is not a regular file"
            )
        return target

    def _read_bytes(self, target: Path) -> bytes:
        try:
            if target.stat().st_size > self._max_bytes:
                raise ProductionScriptSizeException(
                    "production script exceeds the configured limit"
                )
            with target.open("rb") as stream:
                content = stream.read(self._max_bytes + 1)
            if len(content) > self._max_bytes:
                raise ProductionScriptSizeException(
                    "production script exceeds the configured limit"
                )
            return content
        except ProductionScriptSizeException:
            raise
        except (BlockingIOError, PermissionError) as exc:
            raise ProductionScriptTransientReadException(
                "production script could not be read temporarily"
            ) from exc
        except FileNotFoundError as exc:
            raise ProductionScriptMissingFileException(
                "production script file is missing"
            ) from exc
        except OSError as exc:
            raise ProductionScriptTransientReadException(
                "production script could not be read temporarily"
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
