"""Workspace confinement shared by binary asset readers and writers."""

import os
import stat
from pathlib import Path, PurePosixPath

from backend.src.production.binary_assets.exceptions import (
    BinaryAssetLinkError,
    BinaryAssetPathError,
)
from backend.src.production.domain.path_rules import validate_relative_path

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class WorkspaceConfinement:
    """Resolve only normalized relative paths below one trusted workspace."""

    def __init__(self, workspace: Path) -> None:
        expanded = workspace.expanduser()
        self._reject_link_or_reparse(expanded, allow_missing=True)
        self._root = expanded.resolve()
        self._reject_link_or_reparse(self._root, allow_missing=True)

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, relative_path: str, *, require_exists: bool = False) -> Path:
        try:
            normalized = validate_relative_path(relative_path)
        except ValueError as exc:
            raise BinaryAssetPathError("binary asset path is invalid") from exc
        if "\\" in normalized:
            raise BinaryAssetPathError(
                "binary asset path must use POSIX separators"
            )
        target = self._root.joinpath(*PurePosixPath(normalized).parts)
        try:
            target.resolve(strict=False).relative_to(self._root)
        except (OSError, ValueError) as exc:
            raise BinaryAssetPathError("binary asset path escaped workspace") from exc
        self.reject_unsafe_components(target)
        if require_exists and not target.exists():
            from backend.src.production.binary_assets.exceptions import (
                BinaryAssetNotFoundError,
            )

            raise BinaryAssetNotFoundError("binary asset file is missing")
        return target

    def reject_unsafe_components(self, target: Path) -> None:
        try:
            relative = target.relative_to(self._root)
        except ValueError as exc:
            raise BinaryAssetPathError("binary asset path escaped workspace") from exc
        self._reject_link_or_reparse(self._root, allow_missing=True)
        current = self._root
        for part in relative.parts:
            current /= part
            self._reject_link_or_reparse(current, allow_missing=True)

    @staticmethod
    def reject_unsafe_file(path: Path) -> None:
        WorkspaceConfinement._reject_link_or_reparse(path, allow_missing=False)
        try:
            status = os.lstat(path)
        except OSError as exc:
            raise BinaryAssetPathError("binary asset file could not be inspected") from exc
        if not stat.S_ISREG(status.st_mode):
            raise BinaryAssetPathError("binary asset must be a regular file")
        if status.st_nlink != 1:
            raise BinaryAssetLinkError("binary asset hard links are not allowed")

    @staticmethod
    def _reject_link_or_reparse(path: Path, *, allow_missing: bool) -> None:
        try:
            status = os.lstat(path)
        except FileNotFoundError:
            if allow_missing:
                return
            raise
        except OSError as exc:
            raise BinaryAssetPathError("binary asset path could not be inspected") from exc
        attributes = getattr(status, "st_file_attributes", 0)
        if stat.S_ISLNK(status.st_mode) or attributes & _REPARSE_POINT:
            raise BinaryAssetLinkError(
                "binary asset path cannot contain links or junctions"
            )
