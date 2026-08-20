"""Owned notebook producer sessions for inspect-then-capture workflows."""

from __future__ import annotations

import math
import os
import tempfile
import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from marimo_export._diagnostics import record_cleanup_failure
from marimo_export._notebook import (
    _notebook_path,
    _read_stable_source,
    _SourceRevision,
)
from marimo_export._remote.managed import ManagedServer
from marimo_export.client import (
    Client,
    Session,
    _CaptureData,
)
from marimo_export.errors import ExecutionError, SessionError
from marimo_export.index import NotebookProvenance
from marimo_export.inspection import SessionDescription
from marimo_export.spec import ExportSpec, StrPath

if TYPE_CHECKING:
    from marimo_export.limits import CaptureLimits


class OwnedNotebook:
    """One managed notebook session that executes its initial autorun once."""

    __slots__ = (
        "_activation_seconds",
        "_client",
        "_closed",
        "_entered",
        "_initial_source_revision",
        "_initial_source_sha256",
        "_server",
        "_server_shutdown_seconds",
        "_server_start_seconds",
        "_session",
        "_snapshot",
        "_source",
        "_timeout",
    )

    def __init__(self, notebook: StrPath, *, timeout: float = 30.0) -> None:
        self._source = _notebook_path(notebook)
        self._timeout = _timeout(timeout)
        self._initial_source_sha256: str | None = None
        self._initial_source_revision: _SourceRevision | None = None
        self._snapshot: Path | None = None
        self._server: ManagedServer | None = None
        self._client: Client | None = None
        self._session: Session | None = None
        self._entered = False
        self._closed = False
        self._server_start_seconds = 0.0
        self._activation_seconds = 0.0
        self._server_shutdown_seconds = 0.0

    @property
    def path(self) -> Path:
        return self._source

    def __enter__(self) -> OwnedNotebook:
        if self._entered or self._closed:
            raise SessionError(
                "owned notebook contexts are single use",
                code="owned_notebook_closed",
            )
        self._entered = True
        try:
            _, source_sha256, source_revision = _read_stable_source(self._source)
            self._initial_source_sha256 = source_sha256
            self._initial_source_revision = source_revision
            self._snapshot = _copy_notebook(
                self._source,
                expected_sha256=source_sha256,
                expected_revision=source_revision,
            )
            self._require_source_stable()
            started = time.monotonic()
            self._server = ManagedServer(
                self._snapshot,
                timeout=self._timeout,
                runtime_notebook=self._source,
            )
            self._server_start_seconds = time.monotonic() - started
            activation = self._server.activate()
            self._server_start_seconds += activation.session_start_seconds
            self._activation_seconds = activation.initial_autorun_seconds
            self._client = Client(
                self._server.base_url,
                access_token=self._server.access_token,
                timeout=self._timeout,
            )
            self._session = self._client.session(self._server.session_id)
            self._require_source_stable()
            return self
        except BaseException as error:
            self._close(error)
            raise

    def __exit__(
        self,
        exception_type: object,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, traceback
        self._close(exception)

    def inspect(self) -> SessionDescription:
        """Inspect the active notebook after its initial autorun."""

        session = self._require_open()
        self._require_source_stable()
        description = session.inspect()
        self._require_source_stable()
        return replace(
            description,
            session_id="managed",
            filename=self._source.name,
            path=str(self._source),
        )

    def _plan(self, spec: ExportSpec) -> Mapping[str, object]:
        session = self._require_open()
        self._require_source_stable()
        value = session._plan(spec)
        self._require_source_stable()
        return value

    def _capture_data(self, spec: ExportSpec, limits: CaptureLimits) -> _CaptureData:
        session = self._require_open()
        if not isinstance(spec, ExportSpec):
            raise TypeError("spec must be an ExportSpec")
        self._require_source_stable()
        captured = session._capture(spec, limits)
        self._require_source_stable()
        return replace(
            captured,
            index=replace(
                captured.index,
                notebook=NotebookProvenance(
                    filename=self._source.name,
                    document_sha256=captured.index.notebook.document_sha256,
                ),
            ),
        )

    def _require_open(self) -> Session:
        if not self._entered or self._closed or self._session is None:
            raise SessionError(
                "owned notebook session is closed",
                code="owned_notebook_closed",
            )
        return self._session

    def _require_source_stable(self) -> None:
        before_sha256 = self._initial_source_sha256
        before_revision = self._initial_source_revision
        if before_sha256 is None or before_revision is None:
            self._require_open()
            raise AssertionError("owned notebook has no source identity")
        _, after_sha256, after_revision = _read_stable_source(self._source)
        if after_sha256 != before_sha256 or after_revision != before_revision:
            raise ExecutionError(
                "the notebook source changed during the owned session",
                code="notebook_changed",
                details={
                    "before": before_sha256,
                    "after": after_sha256,
                    "revision_changed": after_revision != before_revision,
                },
            )

    def _close(self, primary: BaseException | None) -> None:
        if self._closed:
            return
        self._closed = True
        failures: list[tuple[str, BaseException]] = []
        client = self._client
        self._client = None
        self._session = None
        if client is not None:
            try:
                client.close()
            except BaseException as error:
                failures.append(("managed client cleanup", error))
        server = self._server
        self._server = None
        if server is not None:
            started = time.monotonic()
            try:
                server.stop()
            except BaseException as error:
                failures.append(("managed server cleanup", error))
            finally:
                self._server_shutdown_seconds = time.monotonic() - started
        snapshot = self._snapshot
        self._snapshot = None
        if snapshot is not None:
            try:
                _remove_working_notebook(snapshot)
            except BaseException as error:
                failures.append(("managed notebook cleanup", error))
        if primary is not None:
            for label, failure in failures:
                record_cleanup_failure(primary, label, failure)
            return
        if failures:
            _, first = failures[0]
            for label, failure in failures[1:]:
                record_cleanup_failure(first, label, failure)
            raise first


def open_notebook(notebook: StrPath, *, timeout: float = 30.0) -> OwnedNotebook:
    """Create a single-use owned notebook context."""

    return OwnedNotebook(notebook, timeout=timeout)


def _remove_working_notebook(notebook: Path) -> None:
    try:
        notebook.unlink()
    except OSError as error:
        raise ExecutionError(
            "the managed notebook copy could not be removed",
            code="server_shutdown_failed",
            details={"exception_type": type(error).__name__},
        ) from error


def _copy_notebook(
    notebook: Path,
    *,
    expected_sha256: str,
    expected_revision: _SourceRevision,
) -> Path:
    descriptor: int | None = None
    stream: BinaryIO | None = None
    working_notebook: Path | None = None
    try:
        source, source_sha256, source_revision = _read_stable_source(notebook)
        if source_sha256 != expected_sha256 or source_revision != expected_revision:
            raise ExecutionError(
                "the notebook source changed while its managed copy was created",
                code="notebook_changed",
                details={
                    "before": expected_sha256,
                    "after": source_sha256,
                    "revision_changed": source_revision != expected_revision,
                },
            )
        descriptor, filename = tempfile.mkstemp(
            prefix=f".{notebook.stem}.marimo-export-",
            suffix=notebook.suffix,
            dir=notebook.parent,
        )
        working_notebook = Path(filename)
        stream = os.fdopen(descriptor, "wb")
        descriptor = None
        with stream:
            stream.write(source)
            stream.flush()
            os.fsync(stream.fileno())
        return working_notebook
    except BaseException as error:
        cleanup_errors: list[BaseException] = []
        if stream is not None:
            try:
                stream.close()
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
        if working_notebook is not None:
            try:
                working_notebook.unlink()
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
        if not isinstance(error, OSError):
            for cleanup_error in cleanup_errors:
                record_cleanup_failure(error, "managed notebook cleanup", cleanup_error)
            raise
        details = {"exception_type": type(error).__name__}
        if cleanup_errors:
            details["cleanup_exception_type"] = type(cleanup_errors[0]).__name__
        raise ExecutionError(
            "the managed notebook copy could not be created",
            code="server_start_failed",
            details=details,
        ) from error


def _timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("timeout must be a number")
    result = float(value)
    if result <= 0 or not math.isfinite(result):
        raise ValueError("timeout must be a positive finite number")
    return result


__all__ = ["OwnedNotebook", "open_notebook"]
