from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from marimo_export._repository.models import RepositoryIdentity, digest


def default_repository_path() -> Path:
    configured = os.environ.get("MARIMO_EXPORT_REPOSITORY")
    if configured:
        return Path(configured).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "marimo-export" / "repository"
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / "marimo-export" / "repository"
    cache = os.environ.get("XDG_CACHE_HOME")
    root = Path(cache).expanduser() if cache else Path.home() / ".cache"
    return root / "marimo-export" / "repository"


def private_directory(path: Path) -> Path:
    if path.is_symlink():
        raise OSError(f"Export repository directory is a symlink: {path}")
    created = not path.exists()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise OSError(f"Export repository directory is invalid: {path}")
    if os.name != "nt":
        details = path.stat()
        if hasattr(os, "getuid") and details.st_uid != os.getuid():
            raise PermissionError(f"Export repository directory has another owner: {path}")
        os.chmod(path, stat.S_IRWXU)
    if created and path.parent.is_dir():
        sync_directory(path.parent)
    return path


def prepared_state_root(
    root: Path,
    producer_sha256: str,
    output_plan_sha256: str,
    state_fingerprint: str,
) -> Path:
    producer = digest(producer_sha256, "producer_sha256")
    output_plan = digest(output_plan_sha256, "output_plan_sha256")
    state = digest(state_fingerprint, "state_fingerprint")
    return _contained(root, ("prepared-states", producer, output_plan, state))


def prepared_state_path(
    root: Path,
    producer_sha256: str,
    output_plan_sha256: str,
    state_fingerprint: str,
    instance: str,
) -> Path:
    return _contained(
        prepared_state_root(root, producer_sha256, output_plan_sha256, state_fingerprint),
        (digest(instance, "prepared state instance"),),
    )


def export_root(root: Path, identity: RepositoryIdentity) -> Path:
    return _contained(root, ("exports", identity.key))


def export_path(root: Path, identity: RepositoryIdentity, instance: str) -> Path:
    return _contained(export_root(root, identity), (digest(instance, "export instance"),))


def staging_root(root: Path) -> Path:
    return _contained(root, ("staging",))


def _contained(root: Path, parts: tuple[str, ...]) -> Path:
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise OSError(f"Export repository root is invalid: {root}")
    path = root
    for part in parts:
        if not part or part in {".", ".."} or "/" in part or "\\" in part:
            raise ValueError("Export repository path component is invalid")
        path /= part
        if path.is_symlink():
            raise OSError(f"Export repository path is a symlink: {path}")
    if not path.absolute().is_relative_to(root.absolute()):
        raise OSError("Export repository path escapes its root")
    return path


def sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "default_repository_path",
    "export_path",
    "export_root",
    "prepared_state_path",
    "prepared_state_root",
    "private_directory",
    "staging_root",
    "sync_directory",
]
