"""Atomic durable visual asset plan writer with deterministic recovery reads."""

import asyncio
import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from pydantic import Field, ValidationError

from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.path_rules import validate_relative_path
from backend.src.production.runtime.context import StageContext
from backend.src.production.visual_asset_planning.exceptions import (
    VisualAssetPlanningValidationException,
)
from backend.src.production.visual_asset_planning.models import (
    ProductionVisualAssetPlan,
)
from backend.src.production.visual_asset_planning.serialization import (
    serialize_visual_asset_plan,
)


class WrittenVisualAssetPlanningArtifact(ContractModel):
    relative_path: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    visual_asset_plan: ProductionVisualAssetPlan


class VisualAssetPlanningArtifactWriter(Protocol):
    async def read_existing(
        self,
        *,
        context: StageContext,
    ) -> WrittenVisualAssetPlanningArtifact | None: ...

    async def write_visual_asset_plan(
        self,
        *,
        context: StageContext,
        visual_asset_plan: ProductionVisualAssetPlan,
    ) -> WrittenVisualAssetPlanningArtifact: ...


class InMemoryVisualAssetPlanningArtifactWriter:
    def __init__(self) -> None:
        self.contents: dict[str, bytes] = {}

    async def read_existing(
        self,
        *,
        context: StageContext,
    ) -> WrittenVisualAssetPlanningArtifact | None:
        relative_path = visual_asset_plan_relative_path(context)
        content = self.contents.get(relative_path)
        return _decode_written(relative_path, content) if content is not None else None

    async def write_visual_asset_plan(
        self,
        *,
        context: StageContext,
        visual_asset_plan: ProductionVisualAssetPlan,
    ) -> WrittenVisualAssetPlanningArtifact:
        relative_path = visual_asset_plan_relative_path(context)
        content = serialize_visual_asset_plan(visual_asset_plan)
        existing = self.contents.get(relative_path)
        if existing is not None and existing != content:
            raise VisualAssetPlanningValidationException(
                "visual asset plan path already has incompatible content"
            )
        self.contents[relative_path] = content
        return _written(relative_path, content, visual_asset_plan)


class LocalVisualAssetPlanningArtifactWriter:
    def __init__(self, workspace_root: Path, *, max_artifact_bytes: int) -> None:
        if max_artifact_bytes < 1:
            raise ValueError("maximum visual asset plan size must be positive")
        expanded = workspace_root.expanduser()
        if expanded.is_symlink():
            raise ValueError("visual asset planning root cannot be a symbolic link")
        self._root = expanded.resolve()
        self._max_bytes = max_artifact_bytes

    async def read_existing(
        self,
        *,
        context: StageContext,
    ) -> WrittenVisualAssetPlanningArtifact | None:
        relative_path = visual_asset_plan_relative_path(context)
        return await asyncio.to_thread(self._read_existing_sync, relative_path)

    async def write_visual_asset_plan(
        self,
        *,
        context: StageContext,
        visual_asset_plan: ProductionVisualAssetPlan,
    ) -> WrittenVisualAssetPlanningArtifact:
        relative_path = visual_asset_plan_relative_path(context)
        content = serialize_visual_asset_plan(visual_asset_plan)
        if len(content) > self._max_bytes:
            raise VisualAssetPlanningValidationException(
                "visual asset plan exceeds the configured limit"
            )
        await asyncio.to_thread(self._write_atomic, relative_path, content)
        return _written(relative_path, content, visual_asset_plan)

    def _read_existing_sync(
        self,
        relative_path: str,
    ) -> WrittenVisualAssetPlanningArtifact | None:
        target = self._safe_target(relative_path)
        if not target.exists():
            return None
        if not target.is_file():
            raise VisualAssetPlanningValidationException(
                "existing visual asset plan is not a regular file"
            )
        try:
            if target.stat().st_size > self._max_bytes:
                raise VisualAssetPlanningValidationException(
                    "existing visual asset plan exceeds the configured limit"
                )
            with target.open("rb") as stream:
                content = stream.read(self._max_bytes + 1)
        except VisualAssetPlanningValidationException:
            raise
        except OSError as exc:
            raise VisualAssetPlanningValidationException(
                "existing visual asset plan could not be read"
            ) from exc
        if len(content) > self._max_bytes:
            raise VisualAssetPlanningValidationException(
                "existing visual asset plan exceeds the configured limit"
            )
        return _decode_written(relative_path, content)

    def _write_atomic(self, relative_path: str, content: bytes) -> None:
        target = self._safe_target(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        _reject_symlink_components(self._root, target)
        if target.exists():
            if target.is_symlink():
                raise VisualAssetPlanningValidationException(
                    "visual asset plan target cannot be a symbolic link"
                )
            if _read_bounded(target, len(content)) == content:
                return
            raise VisualAssetPlanningValidationException(
                "visual asset plan path already has incompatible content"
            )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            _reject_symlink_components(self._root, target)
            if target.exists() or target.is_symlink():
                raise VisualAssetPlanningValidationException(
                    "visual asset plan target appeared concurrently"
                )
            os.replace(temporary, target)
            _fsync_directory(target.parent)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _safe_target(self, relative_path: str) -> Path:
        target = self._root.joinpath(*PurePosixPath(relative_path).parts)
        try:
            target.resolve(strict=False).relative_to(self._root)
        except ValueError as exc:
            raise VisualAssetPlanningValidationException(
                "visual asset plan escaped workspace root"
            ) from exc
        _reject_symlink_components(self._root, target)
        return target


def visual_asset_plan_relative_path(context: StageContext) -> str:
    relative_path = f"{context.workspace_relative_path}/visual-asset-plan.json"
    normalized = validate_relative_path(relative_path)
    if "\\" in normalized:
        raise VisualAssetPlanningValidationException(
            "visual asset plan path must use POSIX separators"
        )
    expected = (
        f"production/{context.job_id}/visual_asset_planning/"
        f"attempt-{context.attempt_number}/visual-asset-plan.json"
    )
    if normalized != expected:
        raise VisualAssetPlanningValidationException(
            "visual asset plan path is not contractual for this command"
        )
    return normalized


def _decode_written(
    relative_path: str,
    content: bytes,
) -> WrittenVisualAssetPlanningArtifact:
    try:
        decoded = content.decode("utf-8", errors="strict")
        payload = json.loads(
            decoded,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
        plan = ProductionVisualAssetPlan.model_validate(payload)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValidationError,
        TypeError,
        ValueError,
    ) as exc:
        raise VisualAssetPlanningValidationException(
            "existing visual asset plan is invalid"
        ) from exc
    return _written(relative_path, content, plan)


def _written(
    relative_path: str,
    content: bytes,
    plan: ProductionVisualAssetPlan,
) -> WrittenVisualAssetPlanningArtifact:
    return WrittenVisualAssetPlanningArtifact(
        relative_path=relative_path,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        visual_asset_plan=plan,
    )


def _reject_symlink_components(root: Path, target: Path) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise VisualAssetPlanningValidationException(
            "visual asset plan escaped workspace root"
        ) from exc
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise VisualAssetPlanningValidationException(
                "visual asset plan path contains a symbolic link"
            )


def _read_bounded(path: Path, maximum: int) -> bytes:
    with path.open("rb") as stream:
        return stream.read(maximum + 1)


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result
