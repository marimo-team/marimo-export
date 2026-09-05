"""Materialize a prepared export through the existing atomic writer."""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from marimo_export._limits import MAX_EXPORT_ASSET_BYTES
from marimo_export._secure_io import read_export_asset, read_export_index
from marimo_export._writer import WriteResult, materialize_export, write_export
from marimo_export.descriptors import OutputCodec, asset_path
from marimo_export.errors import MarimoExportError, NotebookExportError
from marimo_export.index import ExportIndex
from marimo_export.progress import ProgressEvent
from marimo_export.reader import open_export
from marimo_export.result import ExportResult
from marimo_export.spec import StrPath
from marimo_export.verification import verify_export

if TYPE_CHECKING:
    from marimo_export.prepared import PreparedExport

_MAX_INDEX_BYTES = 16 * 1024 * 1024


class _ArtifactAssets(Mapping[tuple[OutputCodec, str], bytes]):
    def __init__(self, root: Path, index: ExportIndex) -> None:
        self._root = root
        self._assets = {
            (codec, reference.sha256): (asset_path(codec, reference.sha256), reference.size)
            for codec, reference in index.assets()
        }

    def __len__(self) -> int:
        return len(self._assets)

    def __iter__(self) -> Iterator[tuple[OutputCodec, str]]:
        return iter(self._assets)

    def __getitem__(self, identity: tuple[OutputCodec, str]) -> bytes:
        relative, size = self._assets[identity]
        return read_export_asset(
            self._root,
            relative,
            expected_size=size,
            max_bytes=MAX_EXPORT_ASSET_BYTES,
        )


def write_prepared_export(
    prepared: PreparedExport,
    output: StrPath,
    *,
    replace: bool,
    progress: Callable[[ProgressEvent], None] | None,
) -> ExportResult:
    """Copy, verify, and atomically commit one live prepared export."""

    started = time.monotonic()
    if not isinstance(replace, bool):
        raise TypeError("replace must be a boolean")
    index, assets = _prepared_source(prepared)
    written = write_export(
        index,
        assets,
        output,
        replace=replace,
    )
    return _export_result(prepared, written, started=started, progress=progress)


def materialize_prepared_export(
    prepared: PreparedExport,
    output: StrPath,
    *,
    progress: Callable[[ProgressEvent], None] | None,
) -> ExportResult:
    """Write one prepared export into an application-owned staging directory."""

    started = time.monotonic()
    destination = Path(output).expanduser().absolute()
    try:
        destination.mkdir(mode=0o700)
    except OSError as error:
        raise NotebookExportError(
            f"prepared export staging could not be created: {destination}",
            code="export_commit_failed",
        ) from error
    try:
        index, assets = _prepared_source(prepared)
        written = materialize_export(index, assets, destination)
        return _export_result(prepared, written, started=started, progress=progress)
    except BaseException as error:
        with suppress(OSError):
            shutil.rmtree(destination)
        if isinstance(error, MarimoExportError):
            raise
        if isinstance(error, (OSError, RuntimeError, ValueError)):
            raise NotebookExportError(
                f"prepared export materialization failed: {error}",
                code="export_commit_failed",
            ) from error
        raise


def _prepared_source(
    prepared: PreparedExport,
) -> tuple[ExportIndex, _ArtifactAssets]:
    source = prepared.path
    index = ExportIndex.from_bytes(read_export_index(source, max_bytes=_MAX_INDEX_BYTES))
    return index, _ArtifactAssets(source, index)


def _export_result(
    prepared: PreparedExport,
    written: WriteResult,
    *,
    started: float,
    progress: Callable[[ProgressEvent], None] | None,
) -> ExportResult:
    path = written.path
    verification = verify_export(written.path)
    result = ExportResult(
        path=path,
        identity=open_export(path).identity,
        plan=prepared.plan,
        reused=prepared.reused,
        prepared_states=prepared.prepared_states,
        reused_states=prepared.reused_states,
        cache_activity=prepared.cache_activity,
        assets=written.assets,
        asset_bytes=written.asset_bytes,
        index_bytes=written.index_bytes,
        verification=verification,
        warnings=written.warnings,
        elapsed_seconds=time.monotonic() - started,
    )
    if progress is not None:
        progress(
            ProgressEvent(
                kind="write_finished",
                completed=len(prepared.plan.states),
                total=len(prepared.plan.states),
                elapsed_seconds=result.elapsed_seconds,
            )
        )
    return result


__all__ = ["materialize_prepared_export", "write_prepared_export"]
