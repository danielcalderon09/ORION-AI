"""Integration adapter exposing durable speech timing to video generation."""

import asyncio
import hashlib
from pathlib import Path, PurePosixPath
from typing import Protocol
from uuid import UUID

from backend.src.production.binary_assets.exceptions import BinaryAssetError
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.duration_resolution import DurableDurationResolution
from backend.src.production.domain.enums import ArtifactStatus, ArtifactType
from backend.src.production.speech_generation.models import SpeechGenerationManifestStatus
from backend.src.production.speech_generation.serialization import deserialize_speech_manifest
from backend.src.production.video_clip_generation.exceptions import (
    ImageAcquisitionManifestChecksumException,
    ImageAcquisitionManifestIncompleteException,
    ImageAcquisitionManifestSchemaException,
    ImageAcquisitionManifestSizeException,
)


class DurationArtifactInventory(Protocol):
    async def list_for_job(self, job_id: UUID) -> tuple[Artifact, ...]: ...


class DurableSpeechDurationResolutionReader:
    """Read the newest completed speech manifest without mutating historical jobs."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        inventory: DurationArtifactInventory,
        max_manifest_bytes: int,
    ) -> None:
        if not 1_024 <= max_manifest_bytes <= 16_000_000:
            raise ValueError("maximum speech duration manifest size is outside safe limits")
        self._confinement = WorkspaceConfinement(workspace_root)
        self._inventory = inventory
        self._maximum = max_manifest_bytes

    async def read_for_job(self, job_id: UUID) -> DurableDurationResolution | None:
        artifacts = await self._inventory.list_for_job(job_id)
        candidates = tuple(
            artifact
            for artifact in artifacts
            if artifact.artifact_type
            is ArtifactType.PRODUCTION_SPEECH_GENERATION_MANIFEST
            and artifact.status is ArtifactStatus.READY
        )
        if not candidates:
            return None
        selected = max(
            candidates,
            key=lambda artifact: (
                _attempt(artifact.relative_path),
                str(artifact.artifact_id),
            ),
        )
        return await asyncio.to_thread(self._read, selected, job_id)

    def _read(
        self,
        artifact: Artifact,
        job_id: UUID,
    ) -> DurableDurationResolution | None:
        _validate_path(artifact.relative_path, job_id)
        if artifact.size_bytes is None or artifact.sha256 is None:
            raise ImageAcquisitionManifestSchemaException(
                "speech duration manifest integrity metadata is missing"
            )
        try:
            target = self._confinement.resolve(
                artifact.relative_path,
                require_exists=True,
            )
            self._confinement.reject_unsafe_file(target)
            if target.stat().st_size > self._maximum:
                raise ImageAcquisitionManifestSizeException(
                    "speech duration manifest exceeds configured limit"
                )
            with target.open("rb") as stream:
                content = stream.read(self._maximum + 1)
        except ImageAcquisitionManifestSizeException:
            raise
        except (OSError, BinaryAssetError) as exc:
            raise ImageAcquisitionManifestSchemaException(
                "speech duration manifest could not be read safely"
            ) from exc
        if len(content) > self._maximum:
            raise ImageAcquisitionManifestSizeException(
                "speech duration manifest exceeds configured limit"
            )
        if len(content) != artifact.size_bytes or hashlib.sha256(content).hexdigest() != (
            artifact.sha256
        ):
            raise ImageAcquisitionManifestChecksumException(
                "speech duration manifest integrity differs"
            )
        try:
            manifest = deserialize_speech_manifest(content)
        except (TypeError, ValueError) as exc:
            raise ImageAcquisitionManifestSchemaException(
                "speech duration manifest is invalid"
            ) from exc
        if manifest.status is not SpeechGenerationManifestStatus.COMPLETED:
            raise ImageAcquisitionManifestIncompleteException(
                "speech duration manifest is not completed"
            )
        resolution = manifest.duration_resolution
        if resolution is not None and not resolution.accepted:
            raise ImageAcquisitionManifestIncompleteException(
                "speech duration resolution was rejected"
            )
        return resolution


def _validate_path(relative_path: str, job_id: UUID) -> None:
    parts = PurePosixPath(relative_path).parts
    if (
        len(parts) != 5
        or parts[:3] != ("production", str(job_id), "generating_narration")
        or not parts[3].startswith("attempt-")
        or not parts[3][8:].isdigit()
        or int(parts[3][8:]) < 1
        or parts[4] != "speech-generation-manifest.json"
    ):
        raise ImageAcquisitionManifestSchemaException(
            "speech duration manifest path is not contractual"
        )


def _attempt(relative_path: str) -> int:
    parts = PurePosixPath(relative_path).parts
    if len(parts) == 5 and parts[3].startswith("attempt-") and parts[3][8:].isdigit():
        return int(parts[3][8:])
    return -1


__all__ = ["DurableSpeechDurationResolutionReader"]
