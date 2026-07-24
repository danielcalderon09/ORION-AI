"""Durable image acquisition manifest reader for video clip generation."""

import asyncio
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.binary_assets.exceptions import (
    BinaryAssetError,
    BinaryAssetLinkError,
    BinaryAssetNotFoundError,
    BinaryAssetPathError,
)
from backend.src.production.binary_assets.ports import BinaryAssetReader
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.domain.enums import ArtifactType
from backend.src.production.domain.path_rules import validate_relative_path
from backend.src.production.image_acquisition.models import (
    SUPPORTED_IMAGE_ACQUISITION_MANIFEST_VERSIONS,
    ImageAcquisitionEntryStatus,
    ImageAcquisitionManifestStatus,
    ProductionImageAcquisitionManifest,
)
from backend.src.production.runtime.context import StageContext
from backend.src.production.video_clip_generation.exceptions import (
    ImageAcquisitionManifestAmbiguousException,
    ImageAcquisitionManifestChecksumException,
    ImageAcquisitionManifestEncodingException,
    ImageAcquisitionManifestIncompleteException,
    ImageAcquisitionManifestJobException,
    ImageAcquisitionManifestJsonException,
    ImageAcquisitionManifestLinkException,
    ImageAcquisitionManifestMissingFileException,
    ImageAcquisitionManifestNotFoundException,
    ImageAcquisitionManifestPathException,
    ImageAcquisitionManifestSchemaException,
    ImageAcquisitionManifestSizeException,
    ImageAcquisitionManifestTransientReadException,
    ImageAcquisitionManifestTypeException,
    ImageAcquisitionManifestVersionException,
    SourceImageCorruptException,
    SourceImageMissingException,
    SourceImageProvenanceException,
)
from backend.src.production.video_clip_generation.ports import (
    ImageAcquisitionManifestArtifactQueryRepository,
    ImageManifestArtifactCandidate,
    ReadImageAcquisitionManifest,
    VerifiedSourceImage,
)

_SOURCE_METADATA_ALLOWLIST = frozenset(
    {
        "schema_version",
        "source_visual_asset_plan_artifact_id",
        "source_visual_asset_plan_sha256",
        "entry_count",
        "stored_count",
        "provider",
        "requested_model",
        "reported_models",
        "checkpointed",
    }
)


class DurableImageAcquisitionManifestReader:
    def __init__(
        self,
        *,
        workspace_root: Path,
        repository: ImageAcquisitionManifestArtifactQueryRepository,
        binary_reader: BinaryAssetReader,
        max_manifest_bytes: int,
    ) -> None:
        if not 1 <= max_manifest_bytes <= 50_000_000:
            raise ValueError("maximum source manifest size is outside safe limits")
        self._confinement = WorkspaceConfinement(workspace_root)
        self._repository = repository
        self._binary_reader = binary_reader
        self._maximum = max_manifest_bytes

    async def read_for_video_clip_generation(
        self, *, context: StageContext
    ) -> ReadImageAcquisitionManifest:
        selected, content, manifest, digest = await asyncio.to_thread(
            self._read_manifest_sync, context
        )
        images: list[VerifiedSourceImage] = []
        for entry in manifest.entries:
            if entry.binary_artifact_id is None or entry.binary_asset_id is None:
                raise SourceImageMissingException(
                    "image manifest entry has no SOURCE_IMAGE reference"
                )
            artifact = await asyncio.to_thread(
                self._repository.get_source_image,
                job_id=context.job_id,
                artifact_id=entry.binary_artifact_id,
            )
            if artifact is None:
                raise SourceImageMissingException(
                    "referenced SOURCE_IMAGE artifact is missing"
                )
            try:
                resolved = await self._binary_reader.resolve(
                    job_id=context.job_id,
                    asset_id=entry.binary_asset_id,
                    extension=entry.extension or "",
                )
            except BinaryAssetNotFoundError as exc:
                raise SourceImageMissingException(
                    "referenced SOURCE_IMAGE or sidecar is missing"
                ) from exc
            except BinaryAssetError as exc:
                raise SourceImageCorruptException(
                    "referenced SOURCE_IMAGE failed integrity validation"
                ) from exc
            binary = resolved.asset
            metadata = artifact.metadata
            if (
                artifact.job_id != context.job_id
                or artifact.artifact_type is not ArtifactType.SOURCE_IMAGE
                or artifact.relative_path != entry.storage_path
                or artifact.relative_path != binary.storage_path
                or artifact.mime_type != entry.mime_type
                or artifact.mime_type != binary.mime_type
                or artifact.sha256 != entry.sha256
                or artifact.sha256 != binary.sha256
                or artifact.size_bytes != entry.size_bytes
                or artifact.size_bytes != binary.size_bytes
                or artifact.width != entry.width
                or artifact.width != binary.width
                or artifact.height != entry.height
                or artifact.height != binary.height
            ):
                raise SourceImageCorruptException(
                    "SOURCE_IMAGE durable metadata does not match its bytes"
                )
            if (
                binary.metadata.source_visual_asset_id != entry.visual_asset_id
                or metadata.get("source_visual_asset_id") != entry.visual_asset_id
                or metadata.get("source_scene_id") != entry.source_scene_id
                or metadata.get("source_shot_id") != entry.source_shot_id
                or metadata.get("role") != entry.role.value
                or metadata.get("source_visual_asset_plan_artifact_id")
                != str(manifest.source_visual_asset_plan_artifact_id)
                or metadata.get("source_visual_asset_plan_sha256")
                != manifest.source_visual_asset_plan_sha256
                or str(binary.metadata.source_visual_asset_plan_artifact_id)
                != str(manifest.source_visual_asset_plan_artifact_id)
                or binary.metadata.attributes.get(
                    "source_visual_asset_plan_sha256"
                )
                != manifest.source_visual_asset_plan_sha256
            ):
                raise SourceImageProvenanceException(
                    "SOURCE_IMAGE provenance differs from image manifest"
                )
            images.append(
                VerifiedSourceImage(
                    visual_asset_id=entry.visual_asset_id,
                    artifact_id=artifact.artifact_id,
                    binary_asset_id=binary.asset_id,
                    sha256=binary.sha256,
                    size_bytes=binary.size_bytes,
                    mime_type=binary.mime_type,
                    width=binary.width,
                    height=binary.height,
                    scene_id=entry.source_scene_id,
                    shot_id=entry.source_shot_id,
                    scene_number=entry.scene_number,
                    shot_number=entry.shot_number,
                    role=entry.role.value,
                    content=resolved.content,
                    metadata={
                        "source_visual_asset_plan_artifact_id": str(
                            manifest.source_visual_asset_plan_artifact_id
                        ),
                        "source_visual_asset_plan_sha256": (
                            manifest.source_visual_asset_plan_sha256
                        ),
                    },
                )
            )
        return ReadImageAcquisitionManifest(
            manifest=manifest,
            job_id=context.job_id,
            artifact_id=selected.artifact_id,
            sha256=digest,
            size_bytes=len(content),
            schema_version=manifest.schema_version,
            source_images=tuple(images),
            metadata=_safe_metadata(selected.metadata),
        )

    def _read_manifest_sync(
        self, context: StageContext
    ) -> tuple[
        ImageManifestArtifactCandidate,
        bytes,
        ProductionImageAcquisitionManifest,
        str,
    ]:
        candidates = self._repository.list_candidates(job_id=context.job_id)
        selected = self._select(candidates, context)
        if selected.job_id != context.job_id:
            raise ImageAcquisitionManifestJobException(
                "image acquisition manifest belongs to another job"
            )
        if (
            selected.artifact_type
            is not ArtifactType.PRODUCTION_IMAGE_ACQUISITION_MANIFEST
        ):
            raise ImageAcquisitionManifestTypeException(
                "input artifact is not an image acquisition manifest"
            )
        self._validate_path(selected.relative_path, context.job_id)
        if selected.size_bytes is None or selected.sha256 is None:
            raise ImageAcquisitionManifestSchemaException(
                "image acquisition manifest integrity metadata is missing"
            )
        target = self._target(selected.relative_path)
        content = self._read_bytes(target)
        if len(content) != selected.size_bytes:
            raise ImageAcquisitionManifestSizeException(
                "image acquisition manifest size differs from metadata"
            )
        digest = hashlib.sha256(content).hexdigest()
        if digest != selected.sha256.lower():
            raise ImageAcquisitionManifestChecksumException(
                "image acquisition manifest checksum differs from metadata"
            )
        try:
            decoded = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ImageAcquisitionManifestEncodingException(
                "image acquisition manifest is not valid UTF-8"
            ) from exc
        try:
            payload = json.loads(
                decoded,
                parse_constant=_reject_constant,
                object_pairs_hook=_reject_duplicate_keys,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise ImageAcquisitionManifestJsonException(
                "image acquisition manifest is not strict JSON"
            ) from exc
        if isinstance(payload, dict):
            schema_version = payload.get("schema_version")
            if (
                schema_version is not None
                and schema_version
                not in SUPPORTED_IMAGE_ACQUISITION_MANIFEST_VERSIONS
            ):
                raise ImageAcquisitionManifestVersionException(
                    "image acquisition manifest version is unsupported"
                )
            status = payload.get("status")
            entries = payload.get("entries")
            if status in {item.value for item in ImageAcquisitionManifestStatus}:
                if status != ImageAcquisitionManifestStatus.COMPLETED.value:
                    raise ImageAcquisitionManifestIncompleteException(
                        "image acquisition manifest is not completed"
                    )
                if isinstance(entries, list) and any(
                    isinstance(entry, dict)
                    and entry.get("status")
                    != ImageAcquisitionEntryStatus.STORED.value
                    for entry in entries
                ):
                    raise ImageAcquisitionManifestIncompleteException(
                        "image acquisition manifest contains an unstored entry"
                    )
        try:
            manifest = ProductionImageAcquisitionManifest.model_validate(payload)
        except (ValidationError, TypeError, ValueError) as exc:
            if isinstance(payload, dict) and payload.get("schema_version") not in (
                None,
                *SUPPORTED_IMAGE_ACQUISITION_MANIFEST_VERSIONS,
            ):
                raise ImageAcquisitionManifestVersionException(
                    "image acquisition manifest version is unsupported"
                ) from exc
            raise ImageAcquisitionManifestSchemaException(
                "image acquisition manifest failed schema validation"
            ) from exc
        if manifest.schema_version not in SUPPORTED_IMAGE_ACQUISITION_MANIFEST_VERSIONS:
            raise ImageAcquisitionManifestVersionException(
                "image acquisition manifest version is unsupported"
            )
        if manifest.status is not ImageAcquisitionManifestStatus.COMPLETED or any(
            entry.status is not ImageAcquisitionEntryStatus.STORED
            for entry in manifest.entries
        ):
            raise ImageAcquisitionManifestIncompleteException(
                "image acquisition manifest is not completed"
            )
        return selected, content, manifest, digest

    def _select(
        self,
        candidates: tuple[ImageManifestArtifactCandidate, ...],
        context: StageContext,
    ) -> ImageManifestArtifactCandidate:
        by_id = {item.artifact_id: item for item in candidates}
        preferred = tuple(
            by_id[item]
            for item in context.input_artifact_ids
            if item in by_id
        )
        if len(preferred) > 1:
            raise ImageAcquisitionManifestAmbiguousException(
                "multiple image acquisition manifests were supplied"
            )
        if preferred:
            return preferred[0]
        input_artifacts = self._repository.list_input_artifacts(
            artifact_ids=context.input_artifact_ids,
        )
        input_manifests = tuple(
            artifact
            for artifact in input_artifacts.values()
            if artifact.artifact_type
            is ArtifactType.PRODUCTION_IMAGE_ACQUISITION_MANIFEST
        )
        if any(artifact.job_id != context.job_id for artifact in input_manifests):
            raise ImageAcquisitionManifestJobException(
                "input image acquisition manifest belongs to another job"
            )
        if len(input_manifests) > 1:
            raise ImageAcquisitionManifestAmbiguousException(
                "multiple image acquisition manifests were supplied"
            )
        if input_artifacts and not input_manifests:
            raise ImageAcquisitionManifestTypeException(
                "input artifacts do not contain an image acquisition manifest"
            )
        if not candidates:
            raise ImageAcquisitionManifestNotFoundException(
                "no durable image acquisition manifest is registered"
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
    def _validate_path(relative_path: str, job_id: UUID) -> None:
        try:
            normalized = validate_relative_path(relative_path)
        except ValueError as exc:
            raise ImageAcquisitionManifestPathException(
                "image acquisition manifest path is unsafe"
            ) from exc
        parts = PurePosixPath(normalized).parts
        if (
            "\\" in normalized
            or len(parts) != 5
            or parts[:3] != ("production", str(job_id), "acquiring_assets")
            or not parts[3].startswith("attempt-")
            or not parts[3][8:].isdigit()
            or int(parts[3][8:]) < 1
            or parts[4] != "image-acquisition-manifest.json"
        ):
            raise ImageAcquisitionManifestPathException(
                "image acquisition manifest path is not contractual"
            )

    def _target(self, relative_path: str) -> Path:
        try:
            target = self._confinement.resolve(relative_path, require_exists=True)
            self._confinement.reject_unsafe_file(target)
            return target
        except BinaryAssetLinkError as exc:
            raise ImageAcquisitionManifestLinkException(
                "image acquisition manifest path contains a link"
            ) from exc
        except BinaryAssetNotFoundError as exc:
            raise ImageAcquisitionManifestMissingFileException(
                "image acquisition manifest file is missing"
            ) from exc
        except BinaryAssetPathError as exc:
            raise ImageAcquisitionManifestPathException(
                "image acquisition manifest path is unsafe"
            ) from exc

    def _read_bytes(self, target: Path) -> bytes:
        try:
            if target.stat().st_size > self._maximum:
                raise ImageAcquisitionManifestSizeException(
                    "image acquisition manifest exceeds the configured limit"
                )
            with target.open("rb") as stream:
                content = stream.read(self._maximum + 1)
        except ImageAcquisitionManifestSizeException:
            raise
        except FileNotFoundError as exc:
            raise ImageAcquisitionManifestMissingFileException(
                "image acquisition manifest file is missing"
            ) from exc
        except (BlockingIOError, PermissionError, OSError) as exc:
            raise ImageAcquisitionManifestTransientReadException(
                "image acquisition manifest could not be read temporarily"
            ) from exc
        if len(content) > self._maximum:
            raise ImageAcquisitionManifestSizeException(
                "image acquisition manifest exceeds the configured limit"
            )
        return content


def _attempt(path: str) -> int:
    parts = PurePosixPath(path).parts
    if len(parts) == 5 and parts[3].startswith("attempt-"):
        value = parts[3][8:]
        return int(value) if value.isdigit() else -1
    return -1


def _safe_metadata(value: dict[str, Any]) -> dict[str, Any]:
    filtered = {
        key: value[key] for key in sorted(value) if key in _SOURCE_METADATA_ALLOWLIST
    }
    result = validate_safe_json(filtered, path="source_image_manifest.metadata")
    if not isinstance(result, dict):
        raise ImageAcquisitionManifestSchemaException(
            "image acquisition manifest metadata is invalid"
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
