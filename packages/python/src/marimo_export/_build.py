from __future__ import annotations

import hashlib
import math
import os
import stat
import tempfile
from dataclasses import replace
from pathlib import Path

from marimo_export._remote.managed import ManagedServer
from marimo_export._writer import preflight_publication, write_publication
from marimo_export.client import Client, _CaptureData, _publication_result
from marimo_export.errors import ExecutionError
from marimo_export.publication import NotebookProvenance, PublicationResult
from marimo_export.spec import ExportSpec, StrPath


def build(
    notebook: StrPath,
    *,
    spec: ExportSpec,
    output: StrPath,
    timeout: float = 30.0,
    replace: bool = False,
) -> PublicationResult:
    """Publish a notebook through an owned authenticated loopback server."""

    source = _notebook_path(notebook)
    if not isinstance(spec, ExportSpec):
        raise TypeError("spec must be an ExportSpec")
    duration = _timeout(timeout)
    if not isinstance(replace, bool):
        raise TypeError("replace must be a boolean")
    destination = preflight_publication(output, replace=replace)
    before = _source_digest(source)
    captured = _capture_owned(source, spec, duration)
    after = _source_digest(source)
    if after != before:
        raise ExecutionError(
            "the notebook source changed during build",
            code="notebook_changed",
            details={"before": before, "after": after},
        )
    written = write_publication(
        captured.index,
        captured.assets,
        destination,
        replace=replace,
    )
    return _publication_result(
        captured,
        written,
        mode="build",
        session_id=None,
    )


def _capture_owned(
    notebook: Path,
    spec: ExportSpec,
    timeout: float,
) -> _CaptureData:
    server: ManagedServer | None = None
    working_notebook: Path | None = None
    captured: _CaptureData | None = None
    primary: BaseException | None = None
    try:
        working_notebook = _copy_notebook(notebook)
        server = ManagedServer(working_notebook, timeout=timeout)
        server.activate()
        with Client(
            server.base_url,
            access_token=server.access_token,
            timeout=timeout,
        ) as client:
            captured = client.session(server.session_id)._capture(spec)
    except BaseException as error:
        primary = error
    finally:
        if server is not None:
            try:
                server.stop()
            except BaseException as cleanup_error:
                if primary is None:
                    primary = cleanup_error
                else:
                    primary.add_note(
                        f"managed server cleanup also failed: {type(cleanup_error).__name__}"
                    )
        if working_notebook is not None:
            try:
                working_notebook.unlink()
            except BaseException as cleanup_error:
                if primary is None:
                    primary = cleanup_error
                else:
                    primary.add_note(
                        f"managed notebook cleanup also failed: {type(cleanup_error).__name__}"
                    )
    if primary is not None:
        raise primary
    if captured is None:
        raise RuntimeError("managed build produced no capture data")
    index = replace(
        captured.index,
        notebook=NotebookProvenance(
            filename=notebook.name,
            document_sha256=captured.index.notebook.document_sha256,
        ),
    )
    return replace(captured, index=index)


def _copy_notebook(notebook: Path) -> Path:
    try:
        source = notebook.read_bytes()
        with tempfile.NamedTemporaryFile(
            mode="xb",
            prefix=f".{notebook.stem}.marimo-export-",
            suffix=notebook.suffix,
            dir=notebook.parent,
            delete=False,
        ) as stream:
            stream.write(source)
            stream.flush()
            os.fsync(stream.fileno())
            return Path(stream.name)
    except OSError as error:
        raise ExecutionError(
            "the managed notebook copy could not be created",
            code="server_start_failed",
        ) from error


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


def _source_digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(64 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise ExecutionError(
            "the notebook source could not be read",
            code="notebook_changed",
        ) from error
    return digest.hexdigest()


def _timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("timeout must be a number")
    result = float(value)
    if result <= 0 or not math.isfinite(result):
        raise ValueError("timeout must be a positive finite number")
    return result


__all__ = ["build"]
