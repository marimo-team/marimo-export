"""Destination preflight and identity for application directory delivery."""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from marimo_export._directory_security import (
    DirectorySecurityIdentity,
    directory_security_identity,
)


@dataclass(frozen=True, slots=True)
class DirectoryIdentity:
    root_device: int
    root_inode: int
    root_mode: int
    root_uid: int | None
    root_gid: int | None
    root_revision: tuple[int | None, ...]
    root_security: DirectorySecurityIdentity
    descendants: tuple[tuple[object, ...], ...]


class DirectoryTransactionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class DirectoryTarget:
    path: Path
    identity: DirectoryIdentity | None


def preflight_directory(
    destination: str | os.PathLike[str],
    *,
    replace: bool,
) -> DirectoryTarget:
    if not isinstance(destination, (str, os.PathLike)):
        raise TypeError("destination must be a string or path-like object")
    if not isinstance(replace, bool):
        raise TypeError("replace must be a boolean")
    requested = Path(destination).expanduser().absolute()
    if requested.name in {"", ".", ".."}:
        _fail("destination_invalid", "destination must name a directory")
    parent = requested.parent
    try:
        parent_metadata = parent.lstat()
    except OSError as error:
        raise DirectoryTransactionError(
            "destination_invalid",
            f"destination parent could not be inspected: {parent}",
        ) from error
    if not stat.S_ISDIR(parent_metadata.st_mode) or stat.S_ISLNK(parent_metadata.st_mode):
        _fail("destination_invalid", "destination parent must be a real directory")
    if not os.access(parent, os.W_OK | os.X_OK):
        _fail("destination_invalid", "destination parent must be writable")
    try:
        target = parent.resolve(strict=True) / requested.name
    except (OSError, RuntimeError) as error:
        raise DirectoryTransactionError(
            "destination_invalid",
            f"destination parent could not be resolved: {parent}",
        ) from error
    try:
        metadata = requested.lstat()
    except FileNotFoundError:
        return DirectoryTarget(target, None)
    except OSError as error:
        raise DirectoryTransactionError(
            "destination_invalid",
            f"destination could not be inspected: {target}",
        ) from error
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        _fail("destination_invalid", "destination must be a real directory")
    if not replace:
        _fail("destination_exists", f"destination already exists: {target}")
    return DirectoryTarget(target, directory_identity(target))


def new_staging_directory(target: Path) -> Path:
    return Path(
        tempfile.mkdtemp(
            dir=target.parent,
            prefix=f".{target.name}.staging-",
        )
    )


def directory_identity(path: Path) -> DirectoryIdentity | None:
    try:
        root = path.lstat()
    except FileNotFoundError:
        return None
    descendants: list[tuple[object, ...]] = []
    for candidate in sorted(path.rglob("*")):
        details = candidate.lstat()
        descendants.append(
            (
                candidate.relative_to(path).as_posix(),
                details.st_mode,
                details.st_mtime_ns,
                details.st_ctime_ns,
                details.st_size,
                details.st_ino,
            )
        )
    birthtime_ns = getattr(root, "st_birthtime_ns", None)
    if birthtime_ns is None and (birthtime := getattr(root, "st_birthtime", None)) is not None:
        birthtime_ns = int(birthtime * 1_000_000_000)
    return DirectoryIdentity(
        root_device=root.st_dev,
        root_inode=root.st_ino,
        root_mode=root.st_mode,
        root_uid=getattr(root, "st_uid", None),
        root_gid=getattr(root, "st_gid", None),
        root_revision=(
            root.st_size,
            root.st_mtime_ns,
            birthtime_ns,
            getattr(root, "st_file_attributes", None),
            getattr(root, "st_reparse_tag", None),
        ),
        root_security=directory_security_identity(path, root),
        descendants=tuple(descendants),
    )


def _fail(code: str, message: str) -> None:
    raise DirectoryTransactionError(code, message)


__all__ = [
    "DirectoryIdentity",
    "DirectoryTarget",
    "DirectoryTransactionError",
    "directory_identity",
    "new_staging_directory",
    "preflight_directory",
]
