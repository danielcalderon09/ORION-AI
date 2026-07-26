"""Read-only reconciliation for contractual binary image assets."""

import asyncio
import logging
from enum import StrEnum
from pathlib import Path, PurePosixPath
from uuid import UUID

from pydantic import Field

from backend.src.production.binary_assets.configuration import (
    AssetStorageConfiguration,
)
from backend.src.production.binary_assets.exceptions import BinaryAssetError
from backend.src.production.binary_assets.filesystem_store import (
    FilesystemBinaryAssetStore,
    deserialize_binary_asset_metadata,
)
from backend.src.production.binary_assets.models import (
    ProductionBinaryAssetReference,
)
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.domain.base import ContractModel

logger = logging.getLogger(__name__)


class BinaryAssetReconciliationIssueKind(StrEnum):
    METADATA_WITHOUT_FILE = "metadata_without_file"
    FILE_WITHOUT_METADATA = "file_without_metadata"
    INVALID_METADATA = "invalid_metadata"
    HASH_MISMATCH = "hash_mismatch"
    SIZE_MISMATCH = "size_mismatch"
    MIME_INVALID = "mime_invalid"
    CORRUPT_FILE = "corrupt_file"
    UNSAFE_LINK = "unsafe_link"
    UNSAFE_PATH = "unsafe_path"


class BinaryAssetReconciliationIssue(ContractModel):
    kind: BinaryAssetReconciliationIssueKind
    relative_path: str
    detail: str = Field(min_length=1, max_length=300)


class BinaryAssetReconciliationReport(ContractModel):
    scanned: int = Field(default=0, ge=0)
    valid: int = Field(default=0, ge=0)
    issues: tuple[BinaryAssetReconciliationIssue, ...] = ()


class FilesystemBinaryAssetReconciler:
    """Detect integrity drift without deleting or rewriting user assets."""

    def __init__(
        self,
        *,
        configuration: AssetStorageConfiguration,
        store: FilesystemBinaryAssetStore,
    ) -> None:
        self._configuration = configuration
        self._store = store
        self._confinement = WorkspaceConfinement(configuration.workspace)
        self.latest_report = BinaryAssetReconciliationReport()

    async def reconcile(self) -> BinaryAssetReconciliationReport:
        files = await asyncio.to_thread(self._scan_contractual_files)
        issues: list[BinaryAssetReconciliationIssue] = []
        valid = 0
        pairs = _pair_candidates(files)
        for key in sorted(pairs):
            binary_path, metadata_path = pairs[key]
            if binary_path is None and metadata_path is not None:
                issues.append(
                    self._issue(
                        BinaryAssetReconciliationIssueKind.METADATA_WITHOUT_FILE,
                        metadata_path,
                        "durable metadata has no binary file",
                    )
                )
                continue
            if binary_path is not None and metadata_path is None:
                issues.append(
                    self._issue(
                        BinaryAssetReconciliationIssueKind.FILE_WITHOUT_METADATA,
                        binary_path,
                        "binary file has no durable metadata",
                    )
                )
                continue
            assert binary_path is not None and metadata_path is not None
            try:
                self._confinement.reject_unsafe_file(metadata_path)
                content = await asyncio.to_thread(
                    _read_bounded,
                    metadata_path,
                    64_000,
                )
                if len(content) > 64_000:
                    raise ValueError("metadata exceeds safe limit")
                asset = deserialize_binary_asset_metadata(content)
                expected_binary = self._confinement.resolve(asset.storage_path)
                if expected_binary != binary_path:
                    raise ValueError("metadata path does not match binary file")
                await self._store.read(reference=ProductionBinaryAssetReference.from_asset(asset))
                valid += 1
            except (
                BinaryAssetError,
                OSError,
                UnicodeError,
                ValueError,
                TypeError,
            ) as exc:
                kind = _classify_issue(exc)
                issues.append(self._issue(kind, metadata_path, _safe_detail(kind)))
        report = BinaryAssetReconciliationReport(
            scanned=len(pairs),
            valid=valid,
            issues=tuple(issues),
        )
        self.latest_report = report
        logger.info(
            "binary asset reconciliation completed",
            extra={
                "scanned": report.scanned,
                "valid": report.valid,
                "issue_count": len(report.issues),
                "issue_kinds": sorted({issue.kind.value for issue in report.issues}),
            },
        )
        return report

    def _scan_contractual_files(self) -> tuple[Path, ...]:
        production = self._configuration.workspace / "production"
        if not production.exists():
            return ()
        self._confinement.reject_unsafe_components(production)
        result: list[Path] = []
        for job_directory in sorted(production.iterdir(), key=lambda item: item.name):
            try:
                UUID(job_directory.name)
            except ValueError:
                continue
            images = job_directory / "assets" / "images"
            if not images.exists():
                continue
            self._confinement.reject_unsafe_components(images)
            for candidate in sorted(images.iterdir(), key=lambda item: item.name):
                # Every entry in this contractual directory is relevant, including links.
                result.append(candidate)
        return tuple(result)

    def _issue(
        self,
        kind: BinaryAssetReconciliationIssueKind,
        path: Path,
        detail: str,
    ) -> BinaryAssetReconciliationIssue:
        try:
            relative = PurePosixPath(
                *path.relative_to(self._configuration.workspace).parts
            ).as_posix()
        except ValueError:
            relative = "outside-workspace"
        return BinaryAssetReconciliationIssue(
            kind=kind,
            relative_path=relative,
            detail=detail,
        )


def _pair_candidates(
    files: tuple[Path, ...],
) -> dict[str, tuple[Path | None, Path | None]]:
    pairs: dict[str, tuple[Path | None, Path | None]] = {}
    for path in files:
        name = path.name
        if name.endswith(".asset.json"):
            binary_name = name.removesuffix(".asset.json")
            key = str(path.with_name(binary_name))
            binary, _ = pairs.get(key, (None, None))
            pairs[key] = (binary, path)
        elif not name.startswith("."):
            key = str(path)
            _, metadata = pairs.get(key, (None, None))
            pairs[key] = (path, metadata)
    return pairs


def _read_bounded(path: Path, maximum: int) -> bytes:
    with path.open("rb") as stream:
        return stream.read(maximum + 1)


def _classify_issue(exc: Exception) -> BinaryAssetReconciliationIssueKind:
    from backend.src.production.binary_assets.exceptions import (
        BinaryAssetCorruptError,
        BinaryAssetHashError,
        BinaryAssetLinkError,
        BinaryAssetMimeError,
        BinaryAssetPathError,
        BinaryAssetSizeError,
    )

    if isinstance(exc, BinaryAssetLinkError):
        return BinaryAssetReconciliationIssueKind.UNSAFE_LINK
    if isinstance(exc, BinaryAssetPathError):
        return BinaryAssetReconciliationIssueKind.UNSAFE_PATH
    if isinstance(exc, BinaryAssetHashError):
        return BinaryAssetReconciliationIssueKind.HASH_MISMATCH
    if isinstance(exc, BinaryAssetSizeError):
        return BinaryAssetReconciliationIssueKind.SIZE_MISMATCH
    if isinstance(exc, BinaryAssetCorruptError):
        return BinaryAssetReconciliationIssueKind.CORRUPT_FILE
    if isinstance(exc, BinaryAssetMimeError):
        return BinaryAssetReconciliationIssueKind.MIME_INVALID
    if isinstance(exc, (BinaryAssetError, OSError, UnicodeError, ValueError, TypeError)):
        return BinaryAssetReconciliationIssueKind.INVALID_METADATA
    return BinaryAssetReconciliationIssueKind.INVALID_METADATA


def _safe_detail(kind: BinaryAssetReconciliationIssueKind) -> str:
    return {
        BinaryAssetReconciliationIssueKind.INVALID_METADATA: ("durable binary metadata is invalid"),
        BinaryAssetReconciliationIssueKind.HASH_MISMATCH: ("binary checksum differs from metadata"),
        BinaryAssetReconciliationIssueKind.SIZE_MISMATCH: (
            "binary size differs from metadata or exceeds policy"
        ),
        BinaryAssetReconciliationIssueKind.MIME_INVALID: (
            "binary MIME or extension differs from policy"
        ),
        BinaryAssetReconciliationIssueKind.CORRUPT_FILE: ("binary image cannot be decoded safely"),
        BinaryAssetReconciliationIssueKind.UNSAFE_LINK: (
            "binary path contains a link, junction, or hard link"
        ),
        BinaryAssetReconciliationIssueKind.UNSAFE_PATH: (
            "binary path is outside its contractual workspace location"
        ),
        BinaryAssetReconciliationIssueKind.METADATA_WITHOUT_FILE: (
            "durable metadata has no binary file"
        ),
        BinaryAssetReconciliationIssueKind.FILE_WITHOUT_METADATA: (
            "binary file has no durable metadata"
        ),
    }[kind]
