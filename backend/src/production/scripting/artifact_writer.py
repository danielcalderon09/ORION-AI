"""Safe canonical ProductionScript artifact writers."""

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Protocol

from pydantic import Field

from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.path_rules import validate_relative_path
from backend.src.production.runtime.context import StageContext
from backend.src.production.scripting.models import ProductionScript
from backend.src.production.scripting.serialization import serialize_production_script


class WrittenScriptingArtifact(ContractModel):
    relative_path: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ScriptingArtifactWriter(Protocol):
    async def write_script(
        self,
        *,
        context: StageContext,
        script: ProductionScript,
    ) -> WrittenScriptingArtifact: ...


class InMemoryScriptingArtifactWriter:
    def __init__(self) -> None:
        self.contents: dict[str, bytes] = {}

    async def write_script(
        self,
        *,
        context: StageContext,
        script: ProductionScript,
    ) -> WrittenScriptingArtifact:
        relative_path = _script_relative_path(context)
        content = serialize_production_script(script)
        existing = self.contents.get(relative_path)
        if existing is not None and existing != content:
            raise ValueError("scripting artifact path already has incompatible content")
        self.contents[relative_path] = content
        return _written(relative_path, content)


class LocalScriptingArtifactWriter:
    def __init__(self, workspace_root: Path, *, max_script_bytes: int) -> None:
        if max_script_bytes < 1:
            raise ValueError("maximum production script size must be positive")
        expanded = workspace_root.expanduser()
        if expanded.is_symlink():
            raise ValueError("scripting workspace root cannot be a symbolic link")
        self._root = expanded.resolve()
        self._max_bytes = max_script_bytes

    async def write_script(
        self,
        *,
        context: StageContext,
        script: ProductionScript,
    ) -> WrittenScriptingArtifact:
        relative_path = _script_relative_path(context)
        content = serialize_production_script(script)
        if len(content) > self._max_bytes:
            raise ValueError("production script exceeds the configured limit")
        await asyncio.to_thread(self._write_atomic, relative_path, content)
        return _written(relative_path, content)

    def _write_atomic(self, relative_path: str, content: bytes) -> None:
        target = self._root.joinpath(*PurePosixPath(relative_path).parts)
        try:
            target.resolve(strict=False).relative_to(self._root)
        except ValueError as exc:
            raise ValueError("scripting artifact escaped workspace root") from exc
        _reject_symlink_components(self._root, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        _reject_symlink_components(self._root, target)
        if target.exists():
            if target.is_symlink():
                raise ValueError("scripting artifact target cannot be a symbolic link")
            if target.read_bytes() == content:
                return
            raise ValueError("scripting artifact path already has incompatible content")
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
                raise ValueError("scripting artifact target appeared concurrently")
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


def _script_relative_path(context: StageContext) -> str:
    relative_path = f"{context.workspace_relative_path}/production-script.json"
    normalized = validate_relative_path(relative_path)
    if "\\" in normalized:
        raise ValueError("scripting artifact path must use POSIX separators")
    return normalized


def _written(relative_path: str, content: bytes) -> WrittenScriptingArtifact:
    return WrittenScriptingArtifact(
        relative_path=relative_path,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _reject_symlink_components(root: Path, target: Path) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ValueError("scripting artifact escaped workspace root") from exc
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("scripting artifact path contains a symbolic link")
