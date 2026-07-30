"""Verified reader for durable Phase 5H.2 composition outputs."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path, PurePosixPath

from backend.src.production.binary_assets.exceptions import BinaryAssetError
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.enums import ArtifactStatus, ArtifactType
from backend.src.production.media_composition.domain.models import (
    CompositionAssetAvailability,
    CompositionManifestStatus,
)
from backend.src.production.media_composition.exceptions import MediaCompositionError
from backend.src.production.media_composition.serialization import (
    deserialize_media_composition_manifest,
    deserialize_media_composition_plan,
)
from backend.src.production.rendering.exceptions import RenderingSourceError
from backend.src.production.rendering.ports import (
    RenderArtifactInventory,
    RenderStageContext,
    VerifiedCompositionSource,
)


class VerifiedMediaCompositionSourceReader:
    def __init__(
        self,
        *,
        workspace_root: Path,
        inventory: RenderArtifactInventory,
        max_plan_bytes: int,
        max_manifest_bytes: int,
    ) -> None:
        self._confinement = WorkspaceConfinement(workspace_root)
        self._inventory = inventory
        self._max_plan_bytes = max_plan_bytes
        self._max_manifest_bytes = max_manifest_bytes

    async def read(
        self,
        *,
        context: RenderStageContext,
    ) -> VerifiedCompositionSource:
        artifacts = await self._inventory.list_for_job(context.job_id)
        return await asyncio.to_thread(self._read_sync, context, artifacts)

    def _read_sync(
        self,
        context: RenderStageContext,
        artifacts: tuple[Artifact, ...],
    ) -> VerifiedCompositionSource:
        plan_candidates = tuple(
            item for item in artifacts if item.artifact_type is ArtifactType.MEDIA_COMPOSITION_PLAN
        )
        manifest_candidates = tuple(
            item
            for item in artifacts
            if item.artifact_type is ArtifactType.MEDIA_COMPOSITION_MANIFEST
        )
        if not plan_candidates and not manifest_candidates:
            raise RenderingSourceError(
                "source_missing",
                "registered composition plan and manifest are missing",
            )
        if not plan_candidates:
            raise RenderingSourceError(
                "source_plan_missing",
                "registered composition plan is missing",
            )
        if not manifest_candidates:
            raise RenderingSourceError(
                "source_manifest_missing",
                "registered composition manifest is missing",
            )
        plan_artifact = _select_latest(plan_candidates, "composition plan")
        manifest_artifact = _select_latest(manifest_candidates, "composition manifest")
        if _attempt(plan_artifact.relative_path) != _attempt(manifest_artifact.relative_path):
            raise RenderingSourceError(
                "source_conflict",
                "latest composition plan and manifest attempts differ",
            )
        self._validate_artifact(plan_artifact, context, "composition plan")
        self._validate_artifact(manifest_artifact, context, "composition manifest")
        plan_content = self._read_artifact(
            plan_artifact,
            self._max_plan_bytes,
            "composition plan",
        )
        manifest_content = self._read_artifact(
            manifest_artifact,
            self._max_manifest_bytes,
            "composition manifest",
        )
        try:
            plan = deserialize_media_composition_plan(plan_content)
            manifest = deserialize_media_composition_manifest(manifest_content)
        except (MediaCompositionError, TypeError, ValueError) as exc:
            raise RenderingSourceError(
                "source_corrupt",
                "composition plan or manifest JSON is invalid",
            ) from exc
        if (
            plan.job_id != context.job_id
            or manifest.job_id != context.job_id
            or manifest.status is not CompositionManifestStatus.COMPLETE
            or manifest.plan_relative_path != plan_artifact.relative_path
            or manifest.plan_sha256 != plan_artifact.sha256
            or manifest.plan_size_bytes != plan_artifact.size_bytes
            or manifest.plan_fingerprint != plan.plan_fingerprint
            or manifest.timeline_checksum != plan.timeline_checksum
            or manifest.source_fingerprint != plan.source_fingerprint
            or plan_artifact.metadata.get("plan_fingerprint") != plan.plan_fingerprint
            or manifest_artifact.metadata.get("plan_fingerprint") != plan.plan_fingerprint
            or plan_artifact.metadata.get("timeline_checksum") != plan.timeline_checksum
            or manifest_artifact.metadata.get("timeline_checksum") != plan.timeline_checksum
            or plan_artifact.metadata.get("renderer_executed") is not False
            or manifest_artifact.metadata.get("renderer_executed") is not False
        ):
            raise RenderingSourceError(
                "source_stale",
                "composition plan and manifest identities differ",
            )
        inventory = {item.asset_id: item for item in manifest.asset_inventory}
        if set(inventory) != {item.asset_id for item in plan.assets} or any(
            item.availability is not CompositionAssetAvailability.AVAILABLE
            or item.actual_sha256 != item.expected_sha256
            for item in inventory.values()
        ):
            raise RenderingSourceError(
                "source_incomplete",
                "composition source asset validations are incomplete",
            )
        return VerifiedCompositionSource(
            plan_artifact=plan_artifact,
            manifest_artifact=manifest_artifact,
            plan=plan,
            manifest=manifest,
        )

    @staticmethod
    def _validate_artifact(
        artifact: Artifact,
        context: RenderStageContext,
        label: str,
    ) -> None:
        if (
            artifact.job_id != context.job_id
            or artifact.status is not ArtifactStatus.READY
            or artifact.size_bytes is None
            or artifact.size_bytes <= 0
            or artifact.sha256 is None
        ):
            raise RenderingSourceError(
                "source_invalid",
                f"registered {label} artifact is not READY and complete",
            )

    def _read_artifact(
        self,
        artifact: Artifact,
        maximum: int,
        label: str,
    ) -> bytes:
        if artifact.size_bytes is None or artifact.sha256 is None:
            raise RenderingSourceError("source_invalid", f"{label} metadata is incomplete")
        if artifact.size_bytes > maximum:
            raise RenderingSourceError("source_oversized", f"{label} exceeds its read limit")
        try:
            target = self._confinement.resolve(
                artifact.relative_path,
                require_exists=True,
            )
            self._confinement.reject_unsafe_file(target)
            if target.stat().st_size != artifact.size_bytes:
                raise RenderingSourceError("source_stale", f"{label} size differs")
            with target.open("rb") as stream:
                content = stream.read(maximum + 1)
        except RenderingSourceError:
            raise
        except (BinaryAssetError, OSError) as exc:
            raise RenderingSourceError(
                "source_unsafe",
                f"{label} path is unavailable or unsafe",
            ) from exc
        if (
            len(content) != artifact.size_bytes
            or len(content) > maximum
            or hashlib.sha256(content).hexdigest() != artifact.sha256
        ):
            raise RenderingSourceError("source_corrupt", f"{label} checksum differs")
        return content


def _select_latest(candidates: tuple[Artifact, ...], label: str) -> Artifact:
    ready = tuple(item for item in candidates if item.status is ArtifactStatus.READY)
    if not ready:
        raise RenderingSourceError("source_invalid", f"no READY {label} artifact exists")
    latest_attempt = max(_attempt(item.relative_path) for item in ready)
    if latest_attempt < 1:
        raise RenderingSourceError("source_invalid", f"{label} path has no durable attempt")
    latest = tuple(item for item in ready if _attempt(item.relative_path) == latest_attempt)
    if len(latest) != 1:
        raise RenderingSourceError(
            "source_conflict",
            f"multiple conflicting latest {label} artifacts exist",
        )
    return latest[0]


def _attempt(relative_path: str) -> int:
    for part in PurePosixPath(relative_path).parts:
        if part.startswith("attempt-") and part[8:].isdigit():
            return int(part[8:])
    return -1
