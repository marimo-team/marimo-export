"""Verify repository-backed prepared export files before public use."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from marimo_export._json import sha256_bytes
from marimo_export._secure_io import read_export_index
from marimo_export.descriptors import asset_path
from marimo_export.errors import IntegrityError
from marimo_export.index import ExportIndex
from marimo_export.reader import NotebookExport, open_export
from marimo_export.repository import RepositoryError

_MAX_INDEX_BYTES = 16 * 1024 * 1024
_VERIFY_BUFFER_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ClosureMember:
    sha256: str
    size: int


class ArtifactFiles(Protocol):
    @property
    def instance(self) -> str: ...

    @property
    def path(self) -> Path: ...

    def asset(self, relative: str) -> Path | None: ...


def verified_closure(
    artifact: ArtifactFiles,
) -> tuple[NotebookExport, Mapping[str, ClosureMember]]:
    try:
        index_bytes = read_export_index(artifact.path, max_bytes=_MAX_INDEX_BYTES)
    except OSError as error:
        raise RepositoryError("The prepared export index could not be read.") from error
    identity = sha256_bytes(index_bytes)
    if identity != artifact.instance:
        raise IntegrityError("The prepared export index changed after repository commit.")
    opened = open_export(artifact.path)
    if opened.identity != identity:
        raise IntegrityError("The prepared export index changed during verification.")
    index = ExportIndex.from_bytes(index_bytes)
    closure: dict[str, ClosureMember] = {
        "index.json": ClosureMember(sha256=identity, size=len(index_bytes))
    }
    for codec, reference in index.assets():
        relative = asset_path(codec, reference.sha256)
        closure[relative] = ClosureMember(
            sha256=reference.sha256,
            size=reference.size,
        )
    for relative, member in closure.items():
        path = artifact.asset(relative)
        if path is None:
            raise IntegrityError(
                "The prepared export closure is incomplete.",
                details={"path": relative},
            )
        verify_member(path, relative, member)
    return opened, MappingProxyType(closure)


def verify_member(path: Path, relative: str, member: ClosureMember) -> None:
    descriptor: int | None = None
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0)
    )
    try:
        inspected = path.lstat()
        if _invalid_file(inspected):
            raise IntegrityError(
                "The prepared export closure contains an invalid file.",
                details={"path": relative},
            )
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not _same_file(inspected, opened):
            raise IntegrityError(
                "The prepared export file changed while it was opened.",
                details={"path": relative},
            )
        if opened.st_size != member.size:
            raise IntegrityError(
                "The prepared export file size changed after preparation.",
                details={"path": relative},
            )
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, _VERIFY_BUFFER_BYTES)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        reinspected = path.lstat()
        if (
            _revision(opened) != _revision(after)
            or _invalid_file(reinspected)
            or not _same_file(after, reinspected)
        ):
            raise IntegrityError(
                "The prepared export file changed during verification.",
                details={"path": relative},
            )
        if digest.hexdigest() != member.sha256:
            raise IntegrityError(
                "The prepared export file digest changed after preparation.",
                details={"path": relative},
            )
    except IntegrityError:
        raise
    except OSError as error:
        raise RepositoryError(
            "The prepared export file could not be verified.",
            details={"path": relative},
        ) from error
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _invalid_file(value: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(value, "st_file_attributes", 0)
    return (
        stat.S_ISLNK(value.st_mode)
        or not stat.S_ISREG(value.st_mode)
        or bool(reparse_flag and attributes & reparse_flag)
    )


def _revision(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


__all__ = ["ArtifactFiles", "ClosureMember", "verified_closure", "verify_member"]
