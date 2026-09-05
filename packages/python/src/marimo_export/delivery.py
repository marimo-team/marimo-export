"""Stage application files around prepared exports and commit one directory."""

from __future__ import annotations

import os
import shutil
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from threading import RLock

from marimo_export._delivery_validation import (
    discard as _discard,
)
from marimo_export._delivery_validation import (
    materialization_path as _materialization_path,
)
from marimo_export._delivery_validation import (
    relative_directory as _relative_directory,
)
from marimo_export._delivery_validation import (
    verified_file_count as _verified_file_count,
)
from marimo_export._delivery_validation import (
    verify_materialized as _verify_materialized,
)
from marimo_export._directory import (
    DirectoryIdentity,
    DirectoryTarget,
    DirectoryTransactionError,
    commit_directory,
    directory_identity,
    new_staging_directory,
    preflight_directory,
)
from marimo_export._directory import (
    sync_directory as _sync_directory,
)
from marimo_export._services.write_export import (
    materialize_prepared_export as _materialize_prepared_export,
)
from marimo_export.errors import NotebookExportError
from marimo_export.prepared import PreparedExport
from marimo_export.progress import ProgressEvent
from marimo_export.result import ExportResult, ExportWarning
from marimo_export.spec import StrPath


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Committed application-directory facts."""

    path: Path
    files: int
    warnings: tuple[ExportWarning, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.path, Path) or not self.path.is_absolute():
            raise ValueError("delivery result path must be absolute")
        if not isinstance(self.files, int) or isinstance(self.files, bool) or self.files < 0:
            raise ValueError("delivery result files must be a nonnegative integer")
        if any(not isinstance(warning, ExportWarning) for warning in self.warnings):
            raise TypeError("delivery result warnings must contain ExportWarning values")


class StagedDelivery:
    """One owned staging directory for an application-level delivery."""

    __slots__ = (
        "__weakref__",
        "_closed",
        "_finalizer",
        "_lock",
        "_materialized",
        "_notifying",
        "_path",
        "_target",
    )
    _closed: bool
    _finalizer: weakref.finalize
    _lock: RLock
    _materialized: dict[str, tuple[str, DirectoryIdentity]]
    _notifying: bool
    _path: Path
    _target: DirectoryTarget

    def __init__(self) -> None:
        raise TypeError("StagedDelivery values are returned by stage()")

    @classmethod
    def _create(cls, target: DirectoryTarget, path: Path) -> StagedDelivery:
        self = object.__new__(cls)
        self._target = target
        self._path = path
        self._materialized = {}
        self._notifying = False
        self._lock = RLock()
        self._closed = False
        self._finalizer = weakref.finalize(self, _discard, path)
        return self

    @property
    def path(self) -> Path:
        """Return the writable outer staging directory."""

        with self._lock:
            self._require_open()
            return self._path

    def materialize(
        self,
        prepared: PreparedExport,
        at: str | os.PathLike[str],
        *,
        progress: Callable[[ProgressEvent], None] | None = None,
    ) -> ExportResult:
        """Write and verify one prepared export below the staging directory."""

        if not isinstance(prepared, PreparedExport):
            raise TypeError("prepared must be a PreparedExport")
        if progress is not None and not callable(progress):
            raise TypeError("progress must be callable or None")
        relative = _relative_directory(at)
        with self._lock:
            self._require_open()
            key = relative.as_posix()
            if any(
                relative == PurePosixPath(existing)
                or relative.is_relative_to(PurePosixPath(existing))
                or PurePosixPath(existing).is_relative_to(relative)
                for existing in self._materialized
            ):
                raise ValueError(f"prepared export materialization overlaps {key!r}")
            destination = _materialization_path(self._path, relative)
            events: list[ProgressEvent] = []
            result = _materialize_prepared_export(
                prepared,
                destination,
                progress=events.append if progress is not None else None,
            )
            closure = directory_identity(destination)
            if closure is None:
                raise AssertionError("materialized export directory is unavailable")
            self._materialized[key] = (result.identity, closure)
            for event in events:
                self._emit_progress(progress, event)
            return result

    def commit(
        self,
        *,
        guard: Callable[[], None] | None = None,
        progress: Callable[[ProgressEvent], None] | None = None,
    ) -> DeliveryResult:
        """Verify nested exports and install the complete directory with rollback."""

        if guard is not None and not callable(guard):
            raise TypeError("guard must be callable or None")
        if progress is not None and not callable(progress):
            raise TypeError("progress must be callable or None")
        with self._lock:
            self._require_open()
            retired: Path | None = None
            try:
                self._emit_progress(
                    progress,
                    ProgressEvent(kind="delivery_verification_started"),
                )
                started = time.monotonic()
                _verify_materialized(self._path, self._materialized)
                files = _verified_file_count(self._path)
                if guard is not None:
                    guard()
                if progress is not None:
                    self._emit_progress(
                        progress,
                        ProgressEvent(
                            kind="delivery_commit_started",
                            elapsed_seconds=time.monotonic() - started,
                        ),
                    )
                    _verify_materialized(self._path, self._materialized)
                    files = _verified_file_count(self._path)
                retired = commit_directory(
                    self._path,
                    self._target,
                    retain_replaced=True,
                )
            except DirectoryTransactionError as error:
                raise NotebookExportError(
                    str(error),
                    code=error.code,
                ) from error
            except OSError as error:
                raise NotebookExportError(
                    f"delivery commit failed: {error}",
                    code="export_commit_failed",
                ) from error
            self._closed = True
            self._finalizer.detach()
            warnings: list[ExportWarning] = []
            try:
                _sync_directory(self._target.path.parent)
            except OSError:
                warnings.append(
                    ExportWarning(
                        code="export_parent_sync_failed",
                        message=(
                            "The delivery is visible, but its directory entry was not synced."
                        ),
                        details={"path": str(self._target.path)},
                    )
                )
            if retired is not None:
                try:
                    shutil.rmtree(retired)
                except OSError:
                    warnings.append(
                        ExportWarning(
                            code="retired_destination_cleanup_failed",
                            message="The previous delivery remains beside the destination.",
                            details={"path": str(retired)},
                        )
                    )
            return DeliveryResult(
                path=self._target.path,
                files=files,
                warnings=tuple(warnings),
            )

    def close(self) -> None:
        with self._lock:
            if self._notifying:
                raise RuntimeError("A staged delivery progress callback is active.")
            if self._closed:
                return
            self._closed = True
            if self._finalizer.alive:
                self._finalizer()

    def __enter__(self) -> StagedDelivery:
        with self._lock:
            self._require_open()
            return self

    def __exit__(self, *_error: object) -> None:
        self.close()

    def _require_open(self) -> None:
        if self._notifying:
            raise RuntimeError("A staged delivery progress callback is active.")
        if self._closed:
            raise RuntimeError("The staged delivery is closed.")

    def _emit_progress(
        self,
        callback: Callable[[ProgressEvent], None] | None,
        event: ProgressEvent,
    ) -> None:
        if callback is None:
            return
        self._notifying = True
        try:
            callback(event)
        finally:
            self._notifying = False


def stage(
    destination: StrPath,
    *,
    replace: bool = False,
) -> StagedDelivery:
    """Preflight a destination and return its owned sibling staging directory."""

    try:
        target = preflight_directory(destination, replace=replace)
        path = new_staging_directory(target.path)
    except DirectoryTransactionError as error:
        raise NotebookExportError(str(error), code=error.code) from error
    except OSError as error:
        raise NotebookExportError(
            f"delivery staging failed: {error}",
            code="export_commit_failed",
        ) from error
    return StagedDelivery._create(target, path)


__all__ = ["DeliveryResult", "StagedDelivery", "stage"]
