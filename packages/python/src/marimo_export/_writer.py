from __future__ import annotations

import ctypes
import errno
import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marimo_export._json import sha256_bytes
from marimo_export.errors import IntegrityError, PublicationError
from marimo_export.publication import (
    AssetDescriptor,
    OutputCodec,
    PublicationIndex,
    PublicationWarning,
    ScalarDescriptor,
    asset_path,
)
from marimo_export.reader import _validate_asset, open_publication


@dataclass(frozen=True, slots=True)
class WriteResult:
    path: Path
    assets: int
    asset_bytes: int
    index_bytes: int
    warnings: tuple[PublicationWarning, ...]


def write_publication(
    index: PublicationIndex,
    assets: Mapping[tuple[OutputCodec, str], bytes],
    destination: str | os.PathLike[str],
    *,
    replace: bool,
) -> WriteResult:
    """Stage, verify, and atomically commit one immutable publication."""

    if not isinstance(index, PublicationIndex):
        raise TypeError("index must be a PublicationIndex")
    if not isinstance(assets, Mapping):
        raise TypeError("assets must be a mapping")
    if not isinstance(replace, bool):
        raise TypeError("replace must be a boolean")
    target = preflight_publication(destination, replace=replace)
    expected = {(codec, asset.sha256): asset for codec, asset in index.assets()}
    if set(assets) != set(expected):
        raise PublicationError(
            "asset payload keys must exactly match the publication asset closure",
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

    parent = target.parent
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{target.name}.staging-",
            dir=parent,
        )
    )
    retired: Path | None = None
    committed = False
    warnings: list[PublicationWarning] = []
    try:
        asset_directory = staging / "assets"
        asset_directory.mkdir(mode=0o700)
        total = 0
        descriptors: dict[tuple[OutputCodec, str], AssetDescriptor] = {}
        for _, _, descriptor in index.descriptor_entries():
            if isinstance(descriptor, ScalarDescriptor):
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
            _write_file(staging / relative, payload)
            total += len(payload)

        index_bytes = index.to_bytes()
        _write_file(staging / "index.json", index_bytes)
        _sync_directory(asset_directory)
        _sync_directory(staging)
        open_publication(staging).verify()

        retired = _commit(staging, target, replace=replace)
        committed = True
        _sync_directory(parent)
        open_publication(target).verify()

        if retired is not None:
            try:
                shutil.rmtree(retired)
            except OSError:
                warnings.append(
                    PublicationWarning(
                        code="retired_destination_cleanup_failed",
                        message="The previous publication remains beside the destination.",
                        details={"path": str(retired)},
                    )
                )

        return WriteResult(
            path=target,
            assets=len(expected),
            asset_bytes=total,
            index_bytes=len(index_bytes),
            warnings=tuple(warnings),
        )
    except (IntegrityError, PublicationError):
        raise
    except (OSError, RuntimeError, ValueError) as error:
        raise PublicationError(
            f"publication commit failed: {error}",
            code="publication_commit_failed",
        ) from error
    finally:
        if not committed:
            with suppress(OSError):
                shutil.rmtree(staging)


def _destination(value: str | os.PathLike[str]) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise TypeError("destination must be a string or path-like object")
    target = Path(value).expanduser().absolute()
    if target.name in {"", ".", ".."}:
        raise ValueError("destination must name a publication directory")
    return target


def preflight_publication(
    destination: str | os.PathLike[str],
    *,
    replace: bool,
) -> Path:
    """Validate a publication destination before notebook execution."""

    if not isinstance(replace, bool):
        raise TypeError("replace must be a boolean")
    requested = _destination(destination)
    parent = requested.parent
    try:
        parent_metadata = parent.lstat()
    except OSError as error:
        raise PublicationError(
            f"destination parent could not be inspected: {parent}",
            code="destination_invalid",
        ) from error
    if not stat.S_ISDIR(parent_metadata.st_mode):
        raise PublicationError(
            "destination parent must be a directory",
            code="destination_invalid",
        )
    if not os.access(parent, os.W_OK | os.X_OK):
        raise PublicationError(
            "destination parent must be writable",
            code="destination_invalid",
        )
    try:
        target = parent.resolve(strict=True) / requested.name
    except (OSError, RuntimeError) as error:
        raise PublicationError(
            f"destination parent could not be resolved: {parent}",
            code="destination_invalid",
        ) from error

    try:
        target_metadata = requested.lstat()
    except FileNotFoundError:
        _require_atomic_rename()
        return target
    except OSError as error:
        raise PublicationError(
            f"destination could not be inspected: {target}",
            code="destination_invalid",
        ) from error
    if not stat.S_ISDIR(target_metadata.st_mode) or stat.S_ISLNK(target_metadata.st_mode):
        raise PublicationError(
            "destination must be a real directory",
            code="destination_invalid",
        )
    if not replace:
        raise PublicationError(
            f"destination already exists: {target}",
            code="destination_exists",
        )
    _require_atomic_exchange()
    return target


def _write_file(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("publication file write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _commit(staging: Path, target: Path, *, replace: bool) -> Path | None:
    if not replace:
        try:
            _rename_no_replace(staging, target)
        except FileExistsError as error:
            raise PublicationError(
                f"destination already exists: {target}",
                code="destination_exists",
            ) from error
        return None

    try:
        target_metadata = target.lstat()
    except FileNotFoundError:
        try:
            _rename_no_replace(staging, target)
        except FileExistsError:
            return _commit(staging, target, replace=True)
        return None
    if not stat.S_ISDIR(target_metadata.st_mode) or stat.S_ISLNK(target_metadata.st_mode):
        raise PublicationError(
            "destination must be a real directory",
            code="destination_invalid",
        )
    _rename_exchange(staging, target)
    return staging


def _require_atomic_rename() -> None:
    if not _rename_available():
        raise PublicationError(
            "this platform has no atomic no-replace directory rename",
            code="destination_invalid",
        )


def _require_atomic_exchange() -> None:
    if not _exchange_available():
        raise PublicationError(
            "this platform has no atomic directory exchange",
            code="destination_invalid",
        )


def _rename_available() -> bool:
    return sys.platform == "win32" or _rename_symbol() is not None


def _exchange_available() -> bool:
    return _rename_symbol() is not None


def _rename_no_replace(source: Path, target: Path) -> None:
    if sys.platform == "win32":
        os.rename(source, target)
        return
    _rename_with_flag(source, target, 0x00000004 if sys.platform == "darwin" else 1)


def _rename_exchange(source: Path, target: Path) -> None:
    _rename_with_flag(source, target, 2)


def _rename_symbol() -> tuple[Any, int] | None:
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        function = getattr(library, "renameatx_np", None)
        return None if function is None else (function, -2)
    if sys.platform.startswith("linux"):
        function = getattr(library, "renameat2", None)
        return None if function is None else (function, -100)
    return None


def _rename_with_flag(source: Path, target: Path, flag: int) -> None:
    symbol = _rename_symbol()
    if symbol is None:
        raise OSError(errno.ENOSYS, "atomic directory rename is unavailable")
    function, at_fdcwd = symbol
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    result = function(
        at_fdcwd,
        os.fsencode(source),
        at_fdcwd,
        os.fsencode(target),
        flag,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(error_number, os.strerror(error_number), target)
        raise OSError(error_number, os.strerror(error_number), target)


def _sync_directory(path: Path) -> None:
    if os.name != "posix" or not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = ["WriteResult", "preflight_publication", "write_publication"]
