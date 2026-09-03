"""Path and nested-export validation for staged deliveries."""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from marimo_export._directory_target import DirectoryIdentity, directory_identity
from marimo_export._portable import validate_portable_basename
from marimo_export.errors import IntegrityError, NotebookExportError
from marimo_export.reader import open_export


def relative_directory(value: str | os.PathLike[str]) -> PurePosixPath:
    if not isinstance(value, (str, os.PathLike)):
        raise TypeError("at must be a string or path-like object")
    source = os.fspath(value)
    if not isinstance(source, str) or not source or "\\" in source:
        raise ValueError("at must be a portable relative directory")
    path = PurePosixPath(source)
    if path.is_absolute() or not path.parts:
        raise ValueError("at must be a portable relative directory")
    try:
        for component in path.parts:
            validate_portable_basename(component, "delivery path component")
    except (TypeError, ValueError) as error:
        raise ValueError("at must be a portable relative directory") from error
    return path


def materialization_path(root: Path, relative: PurePosixPath) -> Path:
    parent = root
    for component in relative.parts[:-1]:
        parent /= component
        try:
            details = parent.lstat()
        except FileNotFoundError:
            try:
                parent.mkdir(mode=0o700)
            except FileExistsError:
                details = parent.lstat()
            else:
                continue
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
            raise NotebookExportError(
                "delivery materialization parent must be a real directory",
                code="export_commit_failed",
            )
    return root.joinpath(*relative.parts)


def verify_materialized(
    root: Path,
    exports: Mapping[str, tuple[str, DirectoryIdentity]],
) -> None:
    for relative, (identity, closure) in sorted(exports.items()):
        path = root / relative
        exported = open_export(path)
        exported.verify()
        if exported.identity != identity or directory_identity(path) != closure:
            raise IntegrityError(
                "A materialized export changed before delivery commit.",
                details={"path": relative},
            )


def verified_file_count(root: Path) -> int:
    files = 0
    for candidate in root.rglob("*"):
        details = candidate.lstat()
        if stat.S_ISLNK(details.st_mode):
            raise NotebookExportError(
                "delivery staging contains a symbolic link",
                code="export_commit_failed",
            )
        if stat.S_ISDIR(details.st_mode):
            continue
        if not stat.S_ISREG(details.st_mode):
            raise NotebookExportError(
                "delivery staging contains a special file",
                code="export_commit_failed",
            )
        files += 1
    return files


def discard(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


__all__ = [
    "discard",
    "materialization_path",
    "relative_directory",
    "verified_file_count",
    "verify_materialized",
]
