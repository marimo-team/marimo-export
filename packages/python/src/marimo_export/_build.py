from __future__ import annotations

import hashlib
import math
import os
import stat
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from marimo_export._remote.managed import ManagedServer
from marimo_export._writer import preflight_publication, write_publication
from marimo_export.client import Client, _CaptureData, _publication_result
from marimo_export.errors import ExecutionError
from marimo_export.publication import NotebookProvenance, PhaseTimings, PublicationResult
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

    total_started = time.monotonic()
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
    publication_started = time.monotonic()
    written = write_publication(
        captured.index,
        captured.assets,
        destination,
        replace=replace,
    )
    publication_write_seconds = time.monotonic() - publication_started
    if (
        captured.server_start_seconds is None
        or captured.initial_autorun_seconds is None
        or captured.server_shutdown_seconds is None
    ):
        raise RuntimeError("managed capture produced incomplete phase timings")
    return _publication_result(
        captured,
        written,
        mode="build",
        session_id=None,
        timings=PhaseTimings(
            total_seconds=time.monotonic() - total_started,
            server_start_seconds=captured.server_start_seconds,
            initial_autorun_seconds=captured.initial_autorun_seconds,
            capture_seconds=captured.capture_seconds,
            server_shutdown_seconds=captured.server_shutdown_seconds,
            publication_write_seconds=publication_write_seconds,
            fresh_children=captured.fresh_child_timings,
        ),
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
    server_start_seconds = 0.0
    initial_autorun_seconds = 0.0
    server_shutdown_seconds = 0.0
    try:
        working_notebook = _copy_notebook(notebook)
        server_started = time.monotonic()
        server = ManagedServer(
            working_notebook,
            timeout=timeout,
            runtime_notebook=notebook,
        )
        server_start_seconds = time.monotonic() - server_started
        activation = server.activate()
        server_start_seconds += activation.session_start_seconds
        initial_autorun_seconds = activation.initial_autorun_seconds
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
            shutdown_started = time.monotonic()
            try:
                server.stop()
            except BaseException as cleanup_error:
                if primary is None:
                    primary = cleanup_error
                else:
                    primary.add_note(
                        f"managed server cleanup also failed: {type(cleanup_error).__name__}"
                    )
            finally:
                server_shutdown_seconds = time.monotonic() - shutdown_started
        if working_notebook is not None:
            try:
                _remove_working_notebook(working_notebook)
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
    return replace(
        captured,
        index=index,
        server_start_seconds=server_start_seconds,
        initial_autorun_seconds=initial_autorun_seconds,
        server_shutdown_seconds=server_shutdown_seconds,
    )


def _remove_working_notebook(notebook: Path) -> None:
    try:
        notebook.unlink()
    except OSError as error:
        raise ExecutionError(
            "the managed notebook copy could not be removed",
            code="server_shutdown_failed",
            details={"exception_type": type(error).__name__},
        ) from error


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
