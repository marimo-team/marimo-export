from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias

from marimo_export._json import canonical_bytes
from marimo_export.errors import ExecutionError
from marimo_export.spec import StrPath

if TYPE_CHECKING:
    from marimo_export._marimo.capabilities import NotebookCell

_SourceRevision: TypeAlias = tuple[int, int, int, int, int]

# PEP 723 reference expression as marimo reads it: CRLF lines are accepted and
# a block closes at its first `# ///` line.
_SCRIPT_METADATA = re.compile(
    r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)\r?$\n"
    r"(?P<content>(^#(?! ///\r?$)(| .*)\r?$\n)+)^# ///(?=\r?$)"
)


def document_sha256(notebook: StrPath) -> str:
    """Return the canonical Marimo document digest for one saved notebook."""

    source = _notebook_path(notebook)
    payload, before_sha256, before_revision = _read_stable_source(source)
    from marimo_export._marimo.composition import saved_cells

    digest = cells_document_sha256(saved_cells(source, payload))
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


def cells_document_sha256(cells: Iterable[NotebookCell]) -> str:
    """Return the canonical document digest of one notebook's ordered cells."""

    value = [
        {
            "id": cell.id,
            "code": cell.code.rstrip(),
            "name": cell.name,
            "config": cell.config,
        }
        for cell in cells
    ]
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


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


def _script_metadata(script: str) -> tuple[re.Match[str] | None, str]:
    """Return the PEP 723 `script` block and its TOML content."""

    blocks = [match for match in _SCRIPT_METADATA.finditer(script) if match["type"] == "script"]
    if len(blocks) > 1:
        raise ExecutionError(
            "the notebook has more than one script metadata block",
            code="notebook_invalid",
        )
    if not blocks:
        return None, ""
    content = "".join(
        line[2:] if line.startswith("# ") else line[1:]
        for line in blocks[0]["content"].splitlines(keepends=True)
    ).replace("\r\n", "\n")
    return blocks[0], content


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


__all__ = ["cells_document_sha256", "document_sha256"]
