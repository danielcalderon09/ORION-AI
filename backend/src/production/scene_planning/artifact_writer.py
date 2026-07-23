"""Atomic durable scene-plan writer with deterministic recovery reads."""

import asyncio
import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Protocol

from pydantic import Field, ValidationError

from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.path_rules import validate_relative_path
from backend.src.production.runtime.context import StageContext
from backend.src.production.scene_planning.exceptions import (
    ScenePlanningValidationException,
)
from backend.src.production.scene_planning.models import ProductionScenePlan
from backend.src.production.scene_planning.serialization import serialize_scene_plan


class WrittenScenePlanningArtifact(ContractModel):
    relative_path: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scene_plan: ProductionScenePlan


class ScenePlanningArtifactWriter(Protocol):
    async def read_existing(
        self,
        *,
        context: StageContext,
    ) -> WrittenScenePlanningArtifact | None: ...

    async def write_scene_plan(
        self,
        *,
        context: StageContext,
        scene_plan: ProductionScenePlan,
    ) -> WrittenScenePlanningArtifact: ...


class InMemoryScenePlanningArtifactWriter:
    def __init__(self) -> None:
        self.contents: dict[str, bytes] = {}

    async def read_existing(
        self,
        *,
        context: StageContext,
    ) -> WrittenScenePlanningArtifact | None:
        relative_path = _scene_plan_relative_path(context)
        content = self.contents.get(relative_path)
        return _decode_written(relative_path, content) if content is not None else None

    async def write_scene_plan(
        self,
        *,
        context: StageContext,
        scene_plan: ProductionScenePlan,
    ) -> WrittenScenePlanningArtifact:
        relative_path = _scene_plan_relative_path(context)
        content = serialize_scene_plan(scene_plan)
        existing = self.contents.get(relative_path)
        if existing is not None and existing != content:
            raise ScenePlanningValidationException(
                "scene-plan artifact path already has incompatible content"
            )
        self.contents[relative_path] = content
        return _written(relative_path, content, scene_plan)


class LocalScenePlanningArtifactWriter:
    def __init__(self, workspace_root: Path, *, max_scene_plan_bytes: int) -> None:
        if max_scene_plan_bytes < 1:
            raise ValueError("maximum scene-plan size must be positive")
        expanded = workspace_root.expanduser()
        if expanded.is_symlink():
            raise ValueError("scene-planning workspace root cannot be a symbolic link")
        self._root = expanded.resolve()
        self._max_bytes = max_scene_plan_bytes

    async def read_existing(
        self,
        *,
        context: StageContext,
    ) -> WrittenScenePlanningArtifact | None:
        relative_path = _scene_plan_relative_path(context)
        return await asyncio.to_thread(self._read_existing_sync, relative_path)

    async def write_scene_plan(
        self,
        *,
        context: StageContext,
        scene_plan: ProductionScenePlan,
    ) -> WrittenScenePlanningArtifact:
        relative_path = _scene_plan_relative_path(context)
        content = serialize_scene_plan(scene_plan)
        if len(content) > self._max_bytes:
            raise ScenePlanningValidationException(
                "scene plan exceeds the configured limit"
            )
        await asyncio.to_thread(self._write_atomic, relative_path, content)
        return _written(relative_path, content, scene_plan)

    def _read_existing_sync(
        self,
        relative_path: str,
    ) -> WrittenScenePlanningArtifact | None:
        target = self._safe_target(relative_path)
        if not target.exists():
            return None
        if not target.is_file():
            raise ScenePlanningValidationException(
                "existing scene-plan target is not a regular file"
            )
        try:
            if target.stat().st_size > self._max_bytes:
                raise ScenePlanningValidationException(
                    "existing scene plan exceeds the configured limit"
                )
            content = target.read_bytes()
        except OSError as exc:
            raise ScenePlanningValidationException(
                "existing scene plan could not be read"
            ) from exc
        return _decode_written(relative_path, content)

    def _write_atomic(self, relative_path: str, content: bytes) -> None:
        target = self._safe_target(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        _reject_symlink_components(self._root, target)
        if target.exists():
            if target.is_symlink():
                raise ScenePlanningValidationException(
                    "scene-plan target cannot be a symbolic link"
                )
            if target.read_bytes() == content:
                return
            raise ScenePlanningValidationException(
                "scene-plan path already has incompatible content"
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
                raise ScenePlanningValidationException(
                    "scene-plan target appeared concurrently"
                )
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _safe_target(self, relative_path: str) -> Path:
        target = self._root.joinpath(*PurePosixPath(relative_path).parts)
        try:
            target.resolve(strict=False).relative_to(self._root)
        except ValueError as exc:
            raise ScenePlanningValidationException(
                "scene-plan artifact escaped workspace root"
            ) from exc
        _reject_symlink_components(self._root, target)
        return target


def _scene_plan_relative_path(context: StageContext) -> str:
    relative_path = f"{context.workspace_relative_path}/scene-plan.json"
    normalized = validate_relative_path(relative_path)
    if "\\" in normalized:
        raise ScenePlanningValidationException(
            "scene-plan path must use POSIX separators"
        )
    return normalized


def _decode_written(
    relative_path: str,
    content: bytes,
) -> WrittenScenePlanningArtifact:
    try:
        decoded = content.decode("utf-8", errors="strict")
        payload = json.loads(
            decoded,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
        scene_plan = ProductionScenePlan.model_validate(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise ScenePlanningValidationException(
            "existing scene-plan artifact is invalid"
        ) from exc
    return _written(relative_path, content, scene_plan)


def _written(
    relative_path: str,
    content: bytes,
    scene_plan: ProductionScenePlan,
) -> WrittenScenePlanningArtifact:
    return WrittenScenePlanningArtifact(
        relative_path=relative_path,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        scene_plan=scene_plan,
    )


def _reject_symlink_components(root: Path, target: Path) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ScenePlanningValidationException(
            "scene-plan artifact escaped workspace root"
        ) from exc
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ScenePlanningValidationException(
                "scene-plan path contains a symbolic link"
            )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result
