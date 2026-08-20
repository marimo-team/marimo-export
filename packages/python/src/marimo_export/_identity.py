"""Deterministic identity for one installed marimo-export implementation."""

from __future__ import annotations

import hashlib
from pathlib import Path


class ImplementationDriftError(RuntimeError):
    """The installed sources differ from the code loaded in this process."""

    def __init__(self, loaded: str, current: str) -> None:
        self.loaded = loaded
        self.current = current
        super().__init__("marimo-export sources changed; restart the process before continuing")


def _compute_implementation_identity(root: Path) -> str:
    """Hash one stable Python source tree."""

    before = _source_manifest(root)
    digest = hashlib.sha256()
    try:
        for relative_name, *_ in before:
            relative = relative_name.encode("utf-8")
            payload = root.joinpath(relative_name).read_bytes()
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
    except OSError as error:
        raise RuntimeError("marimo-export sources changed while computing identity") from error
    if _source_manifest(root) != before:
        raise RuntimeError("marimo-export sources changed while computing implementation identity")
    return digest.hexdigest()


def _source_manifest(root: Path) -> tuple[tuple[str, int, int, int, int], ...]:
    try:
        return tuple(
            (
                source.relative_to(root).as_posix(),
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
            )
            for source in sorted(root.rglob("*.py"))
            for metadata in (source.stat(),)
        )
    except OSError as error:
        raise RuntimeError("marimo-export sources could not be inspected") from error


_LOADED_IMPLEMENTATION_IDENTITY = _compute_implementation_identity(Path(__file__).parent)


def implementation_identity() -> str:
    """Return the source identity frozen when this module was loaded."""

    return _LOADED_IMPLEMENTATION_IDENTITY


def require_implementation_stable() -> str:
    """Return the loaded identity after verifying the installed sources."""

    current = _compute_implementation_identity(Path(__file__).parent)
    if current != _LOADED_IMPLEMENTATION_IDENTITY:
        raise ImplementationDriftError(_LOADED_IMPLEMENTATION_IDENTITY, current)
    return _LOADED_IMPLEMENTATION_IDENTITY


__all__ = ["implementation_identity", "require_implementation_stable"]
