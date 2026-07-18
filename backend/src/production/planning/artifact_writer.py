"""Safe content storage port and local/in-memory planning writers."""

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Protocol

from pydantic import Field

from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.path_rules import validate_relative_path
from backend.src.production.planning.models import ProductionPlan
from backend.src.production.planning.serialization import serialize_production_plan
from backend.src.production.runtime.context import StageContext


class WrittenPlanningArtifact(ContractModel):
    relative_path: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class PlanningArtifactWriter(Protocol):
    async def write_plan(
        self,
        *,
        context: StageContext,
        plan: ProductionPlan,
    ) -> WrittenPlanningArtifact: ...


class InMemoryPlanningArtifactWriter:
    def __init__(self) -> None:
        self.contents: dict[str, bytes] = {}

    async def write_plan(
        self,
        *,
        context: StageContext,
        plan: ProductionPlan,
    ) -> WrittenPlanningArtifact:
        relative_path = _plan_relative_path(context)
        content = serialize_production_plan(plan)
        self.contents[relative_path] = content
        return _written(relative_path, content)


class LocalPlanningArtifactWriter:
    """Write canonical JSON atomically beneath an injected workspace root."""

    def __init__(self, workspace_root: Path) -> None:
        self._root = workspace_root.expanduser().resolve()

    async def write_plan(
        self,
        *,
        context: StageContext,
        plan: ProductionPlan,
    ) -> WrittenPlanningArtifact:
        relative_path = _plan_relative_path(context)
        content = serialize_production_plan(plan)
        await asyncio.to_thread(self._write_atomic, relative_path, content)
        return _written(relative_path, content)

    def _write_atomic(self, relative_path: str, content: bytes) -> None:
        target = (self._root / Path(*PurePosixPath(relative_path).parts)).resolve()
        try:
            target.relative_to(self._root)
        except ValueError as exc:
            raise ValueError("planning artifact escaped workspace root") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
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
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


def _plan_relative_path(context: StageContext) -> str:
    relative_path = f"{context.workspace_relative_path}/production-plan.json"
    normalized = validate_relative_path(relative_path)
    if "\\" in normalized:
        raise ValueError("planning artifact path must use POSIX separators")
    return normalized


def _written(relative_path: str, content: bytes) -> WrittenPlanningArtifact:
    return WrittenPlanningArtifact(
        relative_path=relative_path,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
