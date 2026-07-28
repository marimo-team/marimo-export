from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

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
    target = _destination(destination)
    _preflight_destination(target, replace=replace)
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
    parent.mkdir(parents=True, exist_ok=True)
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
    target = Path(value).absolute()
    if target.name in {"", ".", ".."}:
        raise ValueError("destination must name a publication directory")
    return target


def _preflight_destination(target: Path, *, replace: bool) -> None:
    try:
        inspected = target.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise PublicationError(
            f"destination could not be inspected: {target}",
            code="destination_invalid",
        ) from error
    if not target.is_dir() or target.is_symlink():
        raise PublicationError(
            "destination must be a real directory",
            code="destination_invalid",
        )
    if not replace:
        raise PublicationError(
            f"destination already exists: {target}",
            code="destination_exists",
        )
    del inspected
    open_publication(target).verify()


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
        if target.exists() or target.is_symlink():
            raise PublicationError(
                f"destination already exists: {target}",
                code="destination_exists",
            )
        staging.rename(target)
        return None

    retired = target.with_name(f".{target.name}.retired-{uuid.uuid4().hex}")
    target.rename(retired)
    try:
        staging.rename(target)
    except BaseException:
        with suppress(OSError):
            retired.rename(target)
        raise
    return retired


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


__all__ = ["WriteResult", "write_publication"]
