from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import TypeAlias

from marimo_export.errors import ExecutionError
from marimo_export.spec import StrPath

_SourceRevision: TypeAlias = tuple[int, int, int, int, int]


def document_sha256(notebook: StrPath) -> str:
    """Return the canonical Marimo document digest for one saved notebook."""

    source = _notebook_path(notebook)
    payload, before_sha256, before_revision = _read_stable_source(source)
    from marimo_export._marimo.composition import notebook_document_sha256

    digest = notebook_document_sha256(source, payload)
    _, after_sha256, after_revision = _read_stable_source(source)
    if after_sha256 != before_sha256 or after_revision != before_revision:
        raise ExecutionError(
            "the notebook source changed during inspection",
            code="notebook_changed",
            details={
                "before": before_sha256,
                "after": after_sha256,
                "revision_changed": after_revision != before_revision,
            },
        )
    return digest


def _notebook_path(value: StrPath) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise TypeError("notebook must be a string or path-like object")
    requested = Path(value).expanduser().absolute()
    try:
        inspected = requested.lstat()
    except OSError as error:
        raise ValueError(f"notebook is unavailable: {requested}") from error
    if stat.S_ISLNK(inspected.st_mode) or not stat.S_ISREG(inspected.st_mode):
        raise ValueError("notebook must be a real regular file")
    try:
        return requested.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError(f"notebook is unavailable: {requested}") from error


def _read_stable_source(path: Path) -> tuple[bytes, str, _SourceRevision]:
    before = _source_revision(path)
    try:
        source = path.read_bytes()
    except OSError as error:
        raise ExecutionError(
            "the notebook source could not be read",
            code="notebook_changed",
            details={"exception_type": type(error).__name__},
        ) from error
    after = _source_revision(path)
    if after != before:
        raise ExecutionError(
            "the notebook source changed while it was being read",
            code="notebook_changed",
            details={"revision_changed": True},
        )
    return source, hashlib.sha256(source).hexdigest(), after


def _source_revision(path: Path) -> _SourceRevision:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ExecutionError(
            "the notebook source could not be inspected",
            code="notebook_changed",
            details={"exception_type": type(error).__name__},
        ) from error
    if not stat.S_ISREG(metadata.st_mode):
        raise ExecutionError(
            "the notebook source is no longer a regular file",
            code="notebook_changed",
        )
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


__all__ = ["document_sha256"]
