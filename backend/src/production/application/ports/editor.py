"""Professional editing boundary, independent of any concrete editor."""

from dataclasses import dataclass
from typing import Protocol

from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.edit_package import EditPackage


@dataclass(frozen=True, slots=True)
class EditorEnvironmentReport:
    """Result of checking whether an editor adapter can operate safely."""

    available: bool
    adapter_name: str
    adapter_version: str | None = None
    details: str | None = None


@dataclass(frozen=True, slots=True)
class EditorProjectRef:
    """Opaque adapter-owned project reference."""

    external_id: str


@dataclass(frozen=True, slots=True)
class EditorTimelineRef:
    """Opaque adapter-owned timeline reference."""

    external_id: str
    project: EditorProjectRef


@dataclass(frozen=True, slots=True)
class RenderInspection:
    """Technical inspection result for a rendered artifact."""

    valid: bool
    duration_seconds: float | None
    width: int | None
    height: int | None
    fps: float | None
    details: str | None = None


class EditorPort(Protocol):
    """Create and render a timeline through an interchangeable adapter."""

    async def validate_environment(self) -> EditorEnvironmentReport:
        """Check availability and compatibility without changing projects."""
        ...

    async def create_project(self, edit_package: EditPackage) -> EditorProjectRef:
        """Create or recover the idempotent editor project."""
        ...

    async def build_timeline(
        self,
        project: EditorProjectRef,
        edit_package: EditPackage,
    ) -> EditorTimelineRef:
        """Build or recover the idempotent timeline."""
        ...

    async def render(
        self,
        timeline: EditorTimelineRef,
        edit_package: EditPackage,
    ) -> Artifact:
        """Render the package and return a registered artifact contract."""
        ...

    async def inspect_render(self, artifact: Artifact) -> RenderInspection:
        """Inspect the render independently of project creation."""
        ...
