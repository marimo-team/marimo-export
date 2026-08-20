from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from marimo_export._directory import (
    DirectoryTarget,
    DirectoryTransactionError,
    commit_directory,
    new_staging_directory,
    preflight_directory,
)
from marimo_export._directory import (
    sync_directory as _sync_directory,
)
from marimo_export._json import sha256_bytes
from marimo_export.descriptors import (
    AssetDescriptor,
    AssetRef,
    JsonDescriptor,
    OutputCodec,
    ScalarDescriptor,
    asset_path,
)
from marimo_export.errors import IntegrityError, NotebookExportError
from marimo_export.index import ExportIndex
from marimo_export.reader import _validate_asset, open_export
from marimo_export.result import ExportWarning


@dataclass(frozen=True, slots=True)
class WriteResult:
    path: Path
    assets: int
    asset_bytes: int
    index_bytes: int
    warnings: tuple[ExportWarning, ...]


def write_export(
    index: ExportIndex,
    assets: Mapping[tuple[OutputCodec, str], bytes],
    destination: str | os.PathLike[str],
    *,
    replace: bool,
    commit_guard: Callable[[], None] | None = None,
) -> WriteResult:
    """Stage, verify, guard, and atomically commit one immutable export."""

    expected = _expected_assets(index, assets)
    if not isinstance(replace, bool):
        raise TypeError("replace must be a boolean")
    if commit_guard is not None and not callable(commit_guard):
        raise TypeError("commit_guard must be callable or None")
    target = _preflight_export_target(destination, replace=replace)
    parent = target.path.parent
    staging = new_staging_directory(target.path)
    retired: Path | None = None
    committed = False
    warnings: list[ExportWarning] = []
    try:
        materialized = _materialize_export(index, assets, staging, expected)

        if commit_guard is not None:
            commit_guard()
        retired = commit_directory(staging, target, retain_replaced=True)
        committed = True
        try:
            _sync_directory(parent)
        except OSError:
            warnings.append(
                ExportWarning(
                    code="export_parent_sync_failed",
                    message="The export is visible, but its directory entry was not synced.",
                    details={"path": str(target.path)},
                )
            )

        if retired is not None:
            try:
                shutil.rmtree(retired)
            except OSError:
                warnings.append(
                    ExportWarning(
                        code="retired_destination_cleanup_failed",
                        message="The previous export remains beside the destination.",
                        details={"path": str(retired)},
                    )
                )

        return WriteResult(
            path=target.path,
            assets=materialized.assets,
            asset_bytes=materialized.asset_bytes,
            index_bytes=materialized.index_bytes,
            warnings=tuple(warnings),
        )
    except (IntegrityError, NotebookExportError):
        raise
    except DirectoryTransactionError as error:
        raise NotebookExportError(str(error), code=error.code) from error
    except (OSError, RuntimeError, ValueError) as error:
        raise NotebookExportError(
            f"export commit failed: {error}",
            code="export_commit_failed",
        ) from error
    finally:
        if not committed:
            with suppress(OSError):
                shutil.rmtree(staging)


def preflight_export(
    destination: str | os.PathLike[str],
    *,
    replace: bool,
) -> Path:
    """Validate an export destination before notebook execution."""

    return _preflight_export_target(destination, replace=replace).path


def materialize_export(
    index: ExportIndex,
    assets: Mapping[tuple[OutputCodec, str], bytes],
    destination: Path,
) -> WriteResult:
    """Write and verify one export inside an application-owned empty directory."""

    expected = _expected_assets(index, assets)
    return _materialize_export(index, assets, destination, expected)


def _preflight_export_target(
    destination: str | os.PathLike[str],
    *,
    replace: bool,
) -> DirectoryTarget:
    try:
        return preflight_directory(destination, replace=replace)
    except DirectoryTransactionError as error:
        raise NotebookExportError(str(error), code=error.code) from error


def _expected_assets(
    index: ExportIndex,
    assets: Mapping[tuple[OutputCodec, str], bytes],
) -> dict[tuple[OutputCodec, str], AssetRef]:
    if not isinstance(index, ExportIndex):
        raise TypeError("index must be an ExportIndex")
    if not isinstance(assets, Mapping):
        raise TypeError("assets must be a mapping")
    expected = {(codec, asset.sha256): asset for codec, asset in index.assets()}
    if set(assets) != set(expected):
        raise NotebookExportError(
            "asset payload keys must exactly match the export asset closure",
            code="asset_conflict",
            details={
                "missing": [
                    f"{codec}:{digest}" for codec, digest in sorted(set(expected) - set(assets))
                ],
                "extra": [
                    f"{codec}:{digest}" for codec, digest in sorted(set(assets) - set(expected))
                ],
            },
        )
    return expected


def _materialize_export(
    index: ExportIndex,
    assets: Mapping[tuple[OutputCodec, str], bytes],
    destination: Path,
    expected: Mapping[tuple[OutputCodec, str], AssetRef],
) -> WriteResult:
    if not isinstance(destination, Path) or not destination.is_absolute():
        raise ValueError("materialization destination must be an absolute path")
    if not destination.is_dir() or any(destination.iterdir()):
        raise ValueError("materialization destination must be an empty directory")
    asset_directory = destination / "assets"
    asset_directory.mkdir(mode=0o700)
    total = 0
    descriptors: dict[tuple[OutputCodec, str], AssetDescriptor] = {}
    for _, _, descriptor in index.descriptor_entries():
        if isinstance(descriptor, (ScalarDescriptor, JsonDescriptor)):
            continue
        descriptors[(descriptor.codec, descriptor.asset.sha256)] = descriptor
    for identity in sorted(expected):
        codec, digest = identity
        payload = assets[identity]
        if not isinstance(payload, bytes):
            raise TypeError(f"asset payload {codec}:{digest} must be bytes")
        reference = expected[identity]
        if len(payload) != reference.size or sha256_bytes(payload) != digest:
            raise IntegrityError(
                f"asset payload {codec}:{digest} disagrees with its descriptor",
                code="asset_conflict",
            )
        _validate_asset(descriptors[identity], payload)
        relative = asset_path(codec, digest)
        _write_file(destination / relative, payload)
        total += len(payload)

    index_bytes = index.to_bytes()
    _write_file(destination / "index.json", index_bytes)
    _sync_directory(asset_directory)
    _sync_directory(destination)
    open_export(destination).verify()
    return WriteResult(
        path=destination,
        assets=len(expected),
        asset_bytes=total,
        index_bytes=len(index_bytes),
        warnings=(),
    )


def _write_file(path: Path, data: bytes) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
    )
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("export file write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = ["WriteResult", "materialize_export", "preflight_export", "write_export"]
