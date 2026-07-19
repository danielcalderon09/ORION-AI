"""Filesystem reconciler for planning JSON written before durable persistence."""

import asyncio
import errno
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import UUID

from backend.src.production.domain.path_rules import validate_relative_path
from backend.src.production.planning.reconciliation import (
    PlanningArtifactReconciliationError,
    PlanningArtifactReconciliationReport,
    RegisteredPlanningArtifactReader,
)

logger = logging.getLogger(__name__)


class LocalPlanningArtifactReconciler:
    """Quarantine or delete old, unregistered planning artifacts only."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        registered_reader: RegisteredPlanningArtifactReader,
        minimum_age_seconds: float = 300,
        action: str = "quarantine",
        quarantine_relative_path: str = "production-quarantine",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if minimum_age_seconds < 0:
            raise ValueError("planning orphan minimum age cannot be negative")
        if action not in {"delete", "quarantine"}:
            raise ValueError("planning orphan action must be delete or quarantine")
        expanded_root = workspace_root.expanduser()
        if expanded_root.is_symlink():
            raise ValueError("planning workspace root cannot be a symbolic link")
        self._root = expanded_root.resolve()
        self._reader = registered_reader
        self._minimum_age = minimum_age_seconds
        self._action = action
        self._quarantine_relative = validate_relative_path(quarantine_relative_path)
        if "\\" in self._quarantine_relative:
            raise ValueError("planning quarantine path must use POSIX separators")
        self._clock = clock

    async def reconcile(self) -> PlanningArtifactReconciliationReport:
        return await asyncio.to_thread(self._reconcile_sync)

    def _reconcile_sync(self) -> PlanningArtifactReconciliationReport:
        counts = {
            "scanned": 0,
            "registered": 0,
            "orphaned": 0,
            "deleted": 0,
            "quarantined": 0,
            "skipped_recent": 0,
            "errors": 0,
        }
        first_error: Exception | None = None
        try:
            registered = self._reader.list_registered_paths()
            now = self._clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("reconciliation clock must be timezone-aware")
            production_root = self._root / "production"
            if production_root.is_symlink():
                raise ValueError("production workspace cannot be a symbolic link")
            if not production_root.exists():
                return PlanningArtifactReconciliationReport(**counts)
            production_root.resolve().relative_to(self._root)
            for current_name, directory_names, file_names in os.walk(
                production_root,
                topdown=True,
                onerror=_raise_walk_error,
                followlinks=False,
            ):
                current = Path(current_name)
                safe_directories: list[str] = []
                for name in sorted(directory_names):
                    child = current / name
                    if child.is_symlink():
                        counts["errors"] += 1
                        first_error = first_error or ValueError(
                            "planning workspace contains a symbolic link"
                        )
                    else:
                        safe_directories.append(name)
                directory_names[:] = safe_directories
                for name in sorted(file_names):
                    if name != "production-plan.json":
                        continue
                    candidate = current / name
                    counts["scanned"] += 1
                    try:
                        self._reconcile_candidate(
                            candidate=candidate,
                            production_root=production_root,
                            registered=registered,
                            now=now,
                            counts=counts,
                        )
                    except (OSError, ValueError) as exc:
                        counts["errors"] += 1
                        first_error = first_error or exc
        except (OSError, ValueError) as exc:
            counts["errors"] += 1
            first_error = first_error or exc

        report = PlanningArtifactReconciliationReport(**counts)
        logger.info(
            "planning artifact reconciliation completed",
            extra={
                "scanned": report.scanned,
                "registered": report.registered,
                "orphaned": report.orphaned,
                "cleaned": report.deleted + report.quarantined,
                "skipped": report.skipped_recent,
                "error_count": report.errors,
            },
        )
        if first_error is not None:
            raise PlanningArtifactReconciliationError(report) from first_error
        return report

    def _reconcile_candidate(
        self,
        *,
        candidate: Path,
        production_root: Path,
        registered: frozenset[str],
        now: datetime,
        counts: dict[str, int],
    ) -> None:
        if candidate.is_symlink():
            raise ValueError("planning artifact cannot be a symbolic link")
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(production_root.resolve())
        relative = candidate.relative_to(self._root)
        relative_posix = PurePosixPath(*relative.parts).as_posix()
        if not _is_planning_artifact_path(relative):
            return
        if relative_posix in registered:
            counts["registered"] += 1
            return
        counts["orphaned"] += 1
        modified = datetime.fromtimestamp(candidate.stat().st_mtime, tz=UTC)
        if (now - modified).total_seconds() < self._minimum_age:
            counts["skipped_recent"] += 1
            return
        if self._action == "delete":
            candidate.unlink()
            counts["deleted"] += 1
        else:
            destination = self._quarantine_destination(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            _reject_symlink_path(self._root, destination)
            if destination.exists() or destination.is_symlink():
                raise ValueError("planning quarantine destination already exists")
            os.replace(candidate, destination)
            counts["quarantined"] += 1
        _remove_empty_parents(candidate.parent, stop=production_root)

    def _quarantine_destination(self, relative: Path) -> Path:
        quarantine_root = self._root.joinpath(
            *PurePosixPath(self._quarantine_relative).parts
        )
        _reject_symlink_path(self._root, quarantine_root)
        destination = quarantine_root.joinpath(*relative.parts[1:])
        destination.resolve(strict=False).relative_to(self._root)
        return destination


def _is_planning_artifact_path(relative: Path) -> bool:
    parts = relative.parts
    if len(parts) != 5:
        return False
    if parts[0] != "production" or parts[2] != "planning":
        return False
    if not parts[3].startswith("attempt-") or not parts[3][8:].isdigit():
        return False
    if int(parts[3][8:]) < 1 or parts[4] != "production-plan.json":
        return False
    try:
        UUID(parts[1])
    except ValueError:
        return False
    return True


def _reject_symlink_path(root: Path, target: Path) -> None:
    relative = target.relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("planning path contains a symbolic link")


def _remove_empty_parents(directory: Path, *, stop: Path) -> None:
    current = directory
    while current != stop:
        if current.is_symlink():
            raise ValueError("planning directory cannot be a symbolic link")
        try:
            current.rmdir()
        except OSError as exc:
            if exc.errno in {errno.EEXIST, errno.ENOTEMPTY} or getattr(exc, "winerror", None) == 145:
                return
            raise
        current = current.parent


def _raise_walk_error(error: OSError) -> None:
    raise error
