"""Production stage adapter for authorized hybrid visual acquisition."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from uuid import UUID

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome, StageResult
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.enums import ArtifactStatus, ArtifactType, ProductionStage
from backend.src.production.hybrid_runtime.planning import (
    BUDGET_FILENAME,
    STRATEGY_FILENAME,
    HybridRuntimeDriftError,
)
from backend.src.production.image_acquisition.configuration import ImageAcquisitionConfiguration
from backend.src.production.image_acquisition.hybrid_acquisition import (
    HybridAssetAcquisitionCoordinator,
    HybridAssetAcquisitionEntry,
    HybridAssetAcquisitionError,
    HybridAssetAcquisitionManifest,
    HybridAssetAcquisitionManifestWriter,
    HybridAssetAcquisitionSource,
    HybridGeneratedAssetStore,
    ReusableVisualAsset,
    ReusableVisualAssetCatalog,
    StoredGeneratedVisualAsset,
    deserialize_hybrid_acquisition_manifest,
    serialize_hybrid_acquisition_manifest,
)
from backend.src.production.image_acquisition.ports import ImageAcquisitionProvider
from backend.src.production.planning.aggregate_visual_budget import (
    deserialize_aggregate_visual_budget_plan,
)
from backend.src.production.planning.visual_strategy import (
    deserialize_hybrid_visual_strategy_plan,
)
from backend.src.production.runtime.context import StageContext
from backend.src.production.runtime.runtime_models import StageExecutionOutput
from backend.src.production.visual_asset_planning.models import ProductionVisualAssetPlan

ACQUISITION_FILENAME = "hybrid-asset-acquisition-manifest.json"
_MIME_EXTENSION = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


class HybridRuntimeFilesystem:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def resolve(self, relative: str) -> Path:
        target = (self.root / Path(relative)).resolve()
        if target != self.root and self.root not in target.parents:
            raise HybridRuntimeDriftError("hybrid runtime path escaped workspace")
        return target

    def relative(self, target: Path) -> str:
        return target.relative_to(self.root).as_posix()

    def latest(self, job_id: UUID, stage: str, filename: str) -> Path:
        root = self.resolve(f"production/{job_id}/{stage}")
        candidates: list[tuple[int, Path]] = []
        if root.exists():
            for path in root.glob(f"attempt-*/{filename}"):
                try:
                    number = int(path.parent.name.removeprefix("attempt-"))
                except ValueError:
                    continue
                candidates.append((number, path))
        if not candidates:
            raise HybridRuntimeDriftError(f"durable hybrid source is missing: {filename}")
        return max(candidates, key=lambda item: item[0])[1]

    def atomic_replace(self, target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class FilesystemHybridAcquisitionManifestWriter(HybridAssetAcquisitionManifestWriter):
    def __init__(self, filesystem: HybridRuntimeFilesystem, context: StageContext) -> None:
        self._fs = filesystem
        self._context = context
        self._target = filesystem.resolve(f"{context.workspace_relative_path}/{ACQUISITION_FILENAME}")

    async def read(self) -> HybridAssetAcquisitionManifest | None:
        return await asyncio.to_thread(self._read_sync)

    def _read_sync(self) -> HybridAssetAcquisitionManifest | None:
        if self._target.exists():
            return deserialize_hybrid_acquisition_manifest(self._target.read_bytes())
        try:
            previous = self._fs.latest(
                self._context.job_id,
                "acquiring_assets",
                ACQUISITION_FILENAME,
            )
        except HybridRuntimeDriftError:
            return None
        if previous == self._target:
            return None
        return deserialize_hybrid_acquisition_manifest(previous.read_bytes())

    async def create(self, manifest: HybridAssetAcquisitionManifest) -> None:
        if await self.read() is not None:
            raise HybridRuntimeDriftError("hybrid acquisition manifest already exists")
        await asyncio.to_thread(
            self._fs.atomic_replace,
            self._target,
            serialize_hybrid_acquisition_manifest(manifest),
        )

    async def checkpoint(
        self,
        previous: HybridAssetAcquisitionManifest,
        current: HybridAssetAcquisitionManifest,
    ) -> None:
        existing = await self.read()
        if existing != previous:
            raise HybridRuntimeDriftError("hybrid acquisition manifest changed concurrently")
        await asyncio.to_thread(
            self._fs.atomic_replace,
            self._target,
            serialize_hybrid_acquisition_manifest(current),
        )

    @property
    def relative_path(self) -> str:
        return self._fs.relative(self._target)


class FilesystemHybridGeneratedAssetStore(HybridGeneratedAssetStore):
    def __init__(self, filesystem: HybridRuntimeFilesystem) -> None:
        self._fs = filesystem

    async def store_generated(
        self,
        *,
        job_id: UUID,
        entry: HybridAssetAcquisitionEntry,
        content: bytes,
        mime_type: str,
        width: int,
        height: int,
    ) -> StoredGeneratedVisualAsset:
        extension = _MIME_EXTENSION.get(mime_type)
        if extension is None:
            raise HybridAssetAcquisitionError("generated hybrid image MIME is unsupported")
        relative = (
            f"production/{job_id}/hybrid-assets/images/"
            f"{entry.request_identity[:24]}.{extension}"
        )
        target = self._fs.resolve(relative)
        digest = hashlib.sha256(content).hexdigest()
        if target.exists():
            if target.read_bytes() != content:
                raise HybridRuntimeDriftError("generated hybrid image changed during recovery")
        else:
            await asyncio.to_thread(self._fs.atomic_replace, target, content)
        return StoredGeneratedVisualAsset(
            local_asset_id=f"hybrid-image-{entry.request_identity[:24]}",
            sha256=digest,
            mime_type=mime_type,
            width=width,
            height=height,
            storage_reference=relative,
            provenance="orion-hybrid-generated-image-v1",
        )


class FilesystemReusableVisualAssetCatalog(ReusableVisualAssetCatalog):
    """Read explicitly registered immutable reusable assets; never infer candidates."""

    def __init__(self, filesystem: HybridRuntimeFilesystem) -> None:
        self._fs = filesystem

    async def resolve(self, source_asset_id: str) -> ReusableVisualAsset | None:
        safe_name = hashlib.sha256(source_asset_id.encode()).hexdigest()
        target = self._fs.resolve(f"reusable-assets/{safe_name}.json")
        if not target.exists():
            return None
        record = ReusableVisualAsset.model_validate_json(await asyncio.to_thread(target.read_bytes))
        if record.source_asset_id != source_asset_id:
            raise HybridRuntimeDriftError("reusable visual identity differs")
        media = self._fs.resolve(record.storage_reference)
        content = await asyncio.to_thread(media.read_bytes)
        if hashlib.sha256(content).hexdigest() != record.sha256:
            raise HybridRuntimeDriftError("reusable visual checksum differs")
        return record


class HybridAssetAcquisitionStageHandler:
    supported_stages = frozenset({ProductionStage.ACQUIRING_ASSETS})

    def __init__(
        self,
        *,
        workspace_root: Path,
        provider: ImageAcquisitionProvider,
        configuration: ImageAcquisitionConfiguration,
        clock: Callable[[], datetime],
        uuid_factory: Callable[[], UUID],
    ) -> None:
        self._fs = HybridRuntimeFilesystem(workspace_root)
        self._provider = provider
        self._configuration = configuration
        self._clock = clock
        self._uuid_factory = uuid_factory

    async def execute(
        self, command: StageCommand, context: StageContext
    ) -> StageExecutionOutput:
        started = self._aware_now()
        try:
            source = self._read_source(command.job_id)
            writer = FilesystemHybridAcquisitionManifestWriter(self._fs, context)
            coordinator = HybridAssetAcquisitionCoordinator(
                provider=self._provider,
                generated_store=FilesystemHybridGeneratedAssetStore(self._fs),
                reusable_catalog=FilesystemReusableVisualAssetCatalog(self._fs),
                manifest_writer=writer,
                configuration=self._configuration,
            )
            manifest = await coordinator.execute(
                source=source,
                command_id=command.command_id,
                context=context,
            )
            content = serialize_hybrid_acquisition_manifest(manifest)
            manifest_artifact = self._artifact(
                command,
                ArtifactType.HYBRID_ASSET_ACQUISITION_MANIFEST,
                writer.relative_path,
                content,
                {"fingerprint": manifest.fingerprint},
            )
            image_artifacts = tuple(
                self._asset_artifact(command, entry)
                for entry in manifest.entries
                if entry.mime_type is not None and entry.mime_type.startswith("image/")
            )
            artifacts = (manifest_artifact, *image_artifacts)
            result = StageResult(
                command_id=command.command_id,
                job_id=command.job_id,
                stage=command.stage,
                outcome=StageOutcome.SUCCEEDED,
                started_at=started,
                finished_at=self._aware_now(),
                progress_percent=100,
                output_artifact_ids=tuple(item.artifact_id for item in artifacts),
                metadata={
                    "handler": type(self).__name__,
                    "hybrid": True,
                    "strategy_fingerprint": manifest.strategy_fingerprint,
                    "budget_fingerprint": manifest.budget_fingerprint,
                    "acquisition_fingerprint": manifest.fingerprint,
                    "image_requests": sum(
                        1 for item in manifest.entries if item.provider_image_generated
                    ),
                },
            )
            return StageExecutionOutput(result=result, artifacts=artifacts)
        except (HybridAssetAcquisitionError, HybridRuntimeDriftError, ValueError) as exc:
            return StageExecutionOutput(
                result=StageResult(
                    command_id=command.command_id,
                    job_id=command.job_id,
                    stage=command.stage,
                    outcome=StageOutcome.FAILED_PERMANENT,
                    started_at=started,
                    finished_at=self._aware_now(),
                    progress_percent=0,
                    error_code="hybrid_asset_acquisition_failed",
                    error_message=str(exc),
                    metadata={"handler": type(self).__name__},
                )
            )
        except Exception as exc:
            return StageExecutionOutput(
                result=StageResult(
                    command_id=command.command_id,
                    job_id=command.job_id,
                    stage=command.stage,
                    outcome=StageOutcome.FAILED_TRANSIENT,
                    started_at=started,
                    finished_at=self._aware_now(),
                    progress_percent=0,
                    error_code="hybrid_asset_provider_transient",
                    error_message=str(exc),
                    retry_after_seconds=1.0,
                    metadata={"handler": type(self).__name__},
                )
            )

    def _read_source(self, job_id: UUID) -> HybridAssetAcquisitionSource:
        visual_path = self._fs.latest(
            job_id,
            "visual_asset_planning",
            "visual-asset-plan.json",
        )
        strategy_path = self._fs.latest(job_id, "visual_asset_planning", STRATEGY_FILENAME)
        budget_path = self._fs.latest(job_id, "visual_asset_planning", BUDGET_FILENAME)
        visual_content = visual_path.read_bytes()
        return HybridAssetAcquisitionSource(
            visual_asset_plan=ProductionVisualAssetPlan.model_validate_json(visual_content),
            visual_asset_plan_sha256=hashlib.sha256(visual_content).hexdigest(),
            strategy_plan=deserialize_hybrid_visual_strategy_plan(strategy_path.read_bytes()),
            budget_plan=deserialize_aggregate_visual_budget_plan(budget_path.read_bytes()),
        )

    def _asset_artifact(
        self, command: StageCommand, entry: HybridAssetAcquisitionEntry
    ) -> Artifact:
        if entry.storage_reference is None or entry.sha256 is None or entry.mime_type is None:
            raise HybridRuntimeDriftError("resolved hybrid image lacks artifact metadata")
        target = self._fs.resolve(entry.storage_reference)
        return Artifact(
            artifact_id=self._uuid_factory(),
            job_id=command.job_id,
            artifact_type=ArtifactType.SOURCE_IMAGE,
            relative_path=entry.storage_reference,
            mime_type=entry.mime_type,
            status=ArtifactStatus.READY,
            size_bytes=target.stat().st_size,
            sha256=entry.sha256,
            provider="orion-hybrid-assets",
            model_version="hybrid-acquisition-v1",
            metadata={
                "shot_id": entry.shot_id,
                "visual_asset_id": entry.visual_asset_id,
                "visual_mode": entry.visual_mode.value,
                "origin": entry.origin.value,
                "width": entry.width,
                "height": entry.height,
            },
        )

    def _artifact(
        self,
        command: StageCommand,
        artifact_type: ArtifactType,
        relative_path: str,
        content: bytes,
        metadata: dict[str, object],
    ) -> Artifact:
        return Artifact(
            artifact_id=self._uuid_factory(),
            job_id=command.job_id,
            artifact_type=artifact_type,
            relative_path=relative_path,
            mime_type="application/json",
            status=ArtifactStatus.READY,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            provider="orion-local",
            model_version="hybrid-acquisition-v1",
            metadata=metadata,
        )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("hybrid runtime clock must be timezone-aware")
        return value
