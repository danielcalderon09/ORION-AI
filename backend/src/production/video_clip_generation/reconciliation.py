"""Read-only reconciliation for durable video clips and manifests."""

import logging
from enum import StrEnum
from pathlib import Path, PurePosixPath
from uuid import UUID

from pydantic import Field

from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.enums import ProductionStage
from backend.src.production.planning.reconciliation import (
    RegisteredPlanningArtifactReader,
)
from backend.src.production.runtime.context import StageContext
from backend.src.production.video_clip_generation.exceptions import (
    ImageAcquisitionManifestReadError,
)
from backend.src.production.video_clip_generation.models import (
    ProductionVideoClipAsset,
    ProductionVideoClipEntry,
    ProductionVideoClipManifest,
)
from backend.src.production.video_clip_generation.ports import (
    ImageAcquisitionManifestReader,
    ReadImageAcquisitionManifest,
    VideoClipBinaryStore,
)
from backend.src.production.video_clip_generation.serialization import (
    deserialize_video_clip_manifest,
)

logger = logging.getLogger(__name__)


class VideoClipReconciliationIssueKind(StrEnum):
    MANIFEST_WITHOUT_CLIP = "manifest_without_clip"
    CLIP_WITHOUT_SIDECAR = "clip_without_sidecar"
    SIDECAR_WITHOUT_CLIP = "sidecar_without_clip"
    ENTRY_WITHOUT_ARTIFACT = "entry_without_artifact"
    ARTIFACT_WITHOUT_ENTRY = "artifact_without_entry"
    INVALID_MANIFEST = "invalid_manifest"
    INVALID_CLIP = "invalid_clip"
    SOURCE_MISMATCH = "source_mismatch"
    UNSAFE_PATH = "unsafe_path"
    DUPLICATE = "duplicate"


class VideoClipReconciliationIssue(ContractModel):
    kind: VideoClipReconciliationIssueKind
    relative_path: str
    detail: str = Field(min_length=1, max_length=300)


class VideoClipReconciliationReport(ContractModel):
    scanned: int = Field(default=0, ge=0)
    valid: int = Field(default=0, ge=0)
    issues: tuple[VideoClipReconciliationIssue, ...] = ()


class FilesystemVideoClipReconciler:
    """Inspect only contractual video directories; never mutates or regenerates."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        store: VideoClipBinaryStore,
        source_reader: ImageAcquisitionManifestReader,
        registered_reader: RegisteredPlanningArtifactReader,
        max_manifest_bytes: int,
    ) -> None:
        self._root = workspace_root
        self._confinement = WorkspaceConfinement(workspace_root)
        self._store = store
        self._source_reader = source_reader
        self._registered_reader = registered_reader
        self._maximum = max_manifest_bytes
        self.latest_report = VideoClipReconciliationReport()

    async def reconcile(self) -> VideoClipReconciliationReport:
        registered = self._registered_reader.list_registered_paths()
        manifests, clip_files = self._scan()
        issues: list[VideoClipReconciliationIssue] = []
        entry_paths: set[str] = set()
        valid = 0
        for path in manifests:
            relative = self._relative(path)
            try:
                self._confinement.reject_unsafe_file(path)
                content = path.read_bytes()
                if len(content) > self._maximum:
                    raise ValueError("manifest exceeds safe limit")
                manifest = deserialize_video_clip_manifest(content)
                source: ReadImageAcquisitionManifest | None
                try:
                    source = await self._source_reader.read_for_video_clip_generation(
                        context=_source_context(relative, manifest)
                    )
                    if (
                        source.artifact_id
                        != manifest.source_image_manifest_artifact_id
                        or source.sha256 != manifest.source_image_manifest_sha256
                        or source.schema_version
                        != manifest.source_image_manifest_schema_version
                    ):
                        raise ValueError(
                            "source image manifest differs from video manifest"
                        )
                except (ImageAcquisitionManifestReadError, ValueError):
                    source = None
                    issues.append(
                        self._issue(
                            VideoClipReconciliationIssueKind.SOURCE_MISMATCH,
                            relative,
                            "video manifest source images are missing or inconsistent",
                        )
                    )
                source_images = (
                    {
                        image.visual_asset_id: image
                        for image in source.source_images
                    }
                    if source is not None
                    else {}
                )
                seen: set[str] = set()
                for entry in manifest.entries:
                    if entry.visual_asset_id in seen:
                        issues.append(
                            self._issue(
                                VideoClipReconciliationIssueKind.DUPLICATE,
                                relative,
                                "video manifest contains duplicate entries",
                            )
                        )
                    seen.add(entry.visual_asset_id)
                    if entry.storage_path is None:
                        continue
                    entry_paths.add(entry.storage_path)
                    if entry.storage_path not in registered:
                        issues.append(
                            self._issue(
                                VideoClipReconciliationIssueKind.ENTRY_WITHOUT_ARTIFACT,
                                entry.storage_path,
                                "video manifest entry has no registered artifact",
                            )
                        )
                    try:
                        resolved = await self._store.resolve(
                            job_id=_job_id(entry.storage_path),
                            visual_asset_id=entry.visual_asset_id,
                        )
                        asset = resolved.asset
                        if not _asset_matches_entry(asset, entry, manifest):
                            raise ValueError("video clip metadata differs from manifest")
                        image = source_images.get(entry.visual_asset_id)
                        if source is not None and (
                            image is None
                            or image.artifact_id != entry.source_image_artifact_id
                            or image.binary_asset_id
                            != entry.source_image_binary_asset_id
                            or image.sha256 != entry.source_image_sha256
                            or image.scene_id != entry.source_scene_id
                            or image.shot_id != entry.source_shot_id
                            or image.role != entry.role.value
                        ):
                            issues.append(
                                self._issue(
                                    VideoClipReconciliationIssueKind.SOURCE_MISMATCH,
                                    entry.storage_path,
                                    "video clip source provenance is inconsistent",
                                )
                            )
                            continue
                        valid += 1
                    except Exception:
                        issues.append(
                            self._issue(
                                VideoClipReconciliationIssueKind.MANIFEST_WITHOUT_CLIP,
                                entry.storage_path,
                                "video manifest entry has no valid matching clip",
                            )
                        )
            except Exception:
                issues.append(
                    self._issue(
                        VideoClipReconciliationIssueKind.INVALID_MANIFEST,
                        relative,
                        "video clip manifest is invalid or unsafe",
                    )
                )
        for clip_relative in clip_files:
            relative = clip_relative
            if relative.endswith(".asset.json"):
                binary = relative.removesuffix(".asset.json")
                if binary not in clip_files:
                    issues.append(
                        self._issue(
                            VideoClipReconciliationIssueKind.SIDECAR_WITHOUT_CLIP,
                            relative,
                            "video sidecar has no clip",
                        )
                    )
                continue
            sidecar = f"{relative}.asset.json"
            if sidecar not in clip_files:
                issues.append(
                    self._issue(
                        VideoClipReconciliationIssueKind.CLIP_WITHOUT_SIDECAR,
                        relative,
                        "video clip has no durable sidecar",
                    )
                )
        for registered_path in sorted(registered):
            if "/assets/video-clips/" in registered_path:
                if registered_path not in entry_paths:
                    issues.append(
                        self._issue(
                            VideoClipReconciliationIssueKind.ARTIFACT_WITHOUT_ENTRY,
                            registered_path,
                            "registered video artifact has no manifest entry",
                        )
                    )
                if registered_path not in clip_files:
                    issues.append(
                        self._issue(
                            VideoClipReconciliationIssueKind.INVALID_CLIP,
                            registered_path,
                            "registered video artifact file is missing",
                        )
                    )
            elif registered_path.endswith(
                "/video-clip-generation-manifest.json"
            ) and registered_path not in {
                self._relative(item) for item in manifests
            }:
                issues.append(
                    self._issue(
                        VideoClipReconciliationIssueKind.INVALID_MANIFEST,
                        registered_path,
                        "registered video manifest file is missing",
                    )
                )
        report = VideoClipReconciliationReport(
            scanned=len(manifests) + len(clip_files),
            valid=valid,
            issues=tuple(issues),
        )
        self.latest_report = report
        logger.info(
            "video clip reconciliation completed",
            extra={
                "scanned": report.scanned,
                "valid": report.valid,
                "issue_count": len(report.issues),
            },
        )
        return report

    def _scan(self) -> tuple[tuple[Path, ...], frozenset[str]]:
        production = self._root / "production"
        if not production.exists():
            return (), frozenset()
        manifests: list[Path] = []
        clips: set[str] = set()
        for job in sorted(production.iterdir(), key=lambda item: item.name):
            try:
                UUID(job.name)
            except ValueError:
                continue
            stage = job / "generating_video_clips"
            if stage.is_dir():
                for attempt in sorted(stage.iterdir(), key=lambda item: item.name):
                    if (
                        attempt.is_dir()
                        and attempt.name.startswith("attempt-")
                        and attempt.name[8:].isdigit()
                    ):
                        candidate = attempt / "video-clip-generation-manifest.json"
                        if candidate.exists() or candidate.is_symlink():
                            manifests.append(candidate)
            directory = job / "assets" / "video-clips"
            if directory.is_dir():
                for candidate in sorted(directory.iterdir(), key=lambda item: item.name):
                    if candidate.name.startswith("."):
                        continue
                    clips.add(self._relative(candidate))
        return tuple(manifests), frozenset(clips)

    def _relative(self, path: Path) -> str:
        return PurePosixPath(*path.relative_to(self._root).parts).as_posix()

    @staticmethod
    def _issue(
        kind: VideoClipReconciliationIssueKind, path: str, detail: str
    ) -> VideoClipReconciliationIssue:
        return VideoClipReconciliationIssue(
            kind=kind, relative_path=path, detail=detail
        )


def _job_id(relative_path: str) -> UUID:
    parts = PurePosixPath(relative_path).parts
    if (
        len(parts) != 5
        or parts[0] != "production"
        or parts[2:4] != ("assets", "video-clips")
    ):
        raise ValueError("video clip path is not contractual")
    return UUID(parts[1])


def _source_context(
    manifest_path: str,
    manifest: ProductionVideoClipManifest,
) -> StageContext:
    parts = PurePosixPath(manifest_path).parts
    if (
        len(parts) != 5
        or parts[0] != "production"
        or parts[2] != "generating_video_clips"
        or not parts[3].startswith("attempt-")
        or not parts[3][8:].isdigit()
        or parts[4] != "video-clip-generation-manifest.json"
    ):
        raise ValueError("video clip manifest path is not contractual")
    job_id = UUID(parts[1])
    attempt_number = int(parts[3][8:])
    if attempt_number < 1:
        raise ValueError("video clip manifest attempt is invalid")
    return StageContext(
        job_id=job_id,
        command_id=manifest.source_image_manifest_artifact_id,
        stage=ProductionStage.GENERATING_VIDEO_CLIPS,
        attempt_number=attempt_number,
        input_artifact_ids=(manifest.source_image_manifest_artifact_id,),
        workspace_relative_path=PurePosixPath(*parts[:-1]).as_posix(),
        correlation_id=job_id,
    )


def _asset_matches_entry(
    asset: ProductionVideoClipAsset,
    entry: ProductionVideoClipEntry,
    manifest: ProductionVideoClipManifest,
) -> bool:
    metadata = asset.metadata
    return (
        asset.asset_id == entry.video_binary_asset_id
        and asset.storage_path == entry.storage_path
        and asset.mime_type == entry.mime_type == "video/mp4"
        and asset.extension == entry.extension == "mp4"
        and asset.sha256 == entry.sha256
        and asset.size_bytes == entry.size_bytes
        and asset.width == entry.width
        and asset.height == entry.height
        and abs(asset.duration_seconds - (entry.duration_seconds or 0)) <= 0.08
        and abs(asset.frame_rate - (entry.frame_rate or 0)) <= 0.01
        and asset.frame_count == entry.frame_count
        and asset.video_codec == entry.video_codec
        and asset.has_audio is False
        and asset.audio_codec is None
        and asset.scene_id == entry.source_scene_id
        and asset.shot_id == entry.source_shot_id
        and asset.role == entry.role
        and metadata.source_image_manifest_artifact_id
        == manifest.source_image_manifest_artifact_id
        and metadata.source_image_manifest_sha256
        == manifest.source_image_manifest_sha256
        and metadata.source_image_artifact_id == entry.source_image_artifact_id
        and metadata.source_image_binary_asset_id
        == entry.source_image_binary_asset_id
        and metadata.source_image_sha256 == entry.source_image_sha256
        and metadata.source_visual_asset_id == entry.visual_asset_id
        and metadata.source_scene_id == entry.source_scene_id
        and metadata.source_shot_id == entry.source_shot_id
        and metadata.configuration_fingerprint
        == manifest.configuration_fingerprint
    )
