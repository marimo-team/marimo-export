from __future__ import annotations

import errno
import os
import shutil
import stat
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import NoReturn, cast
from uuid import uuid4

from marimo_export._diagnostics import record_cleanup_failure
from marimo_export._json import JsonObject, JsonValue, canonical_bytes
from marimo_export._repository.models import RepositoryIntegrityError, digest
from marimo_export._repository.paths import private_directory, sync_directory
from marimo_export.errors import (
    ExportUnavailableError,
    NotebookExportError,
)
from marimo_export.reader import open_export
from marimo_export.wire import parse_canonical_json, portable_json

PREPARED_STATE_MANIFEST = "prepared-state.json"


@dataclass(frozen=True, slots=True)
class VerifiedClosure:
    files: frozenset[str]
    content_bytes: int


@dataclass(frozen=True, slots=True)
class PreparedStateManifest:
    producer_sha256: str
    output_plan_sha256: str
    state_fingerprint: str
    instance: str
    metadata: JsonObject
    closure: VerifiedClosure


@dataclass(frozen=True, slots=True)
class QuarantinedArtifact:
    original: Path
    quarantine: Path


def write_prepared_state_manifest(
    staging: Path,
    *,
    producer_sha256: str,
    output_plan_sha256: str,
    state_fingerprint: str,
    metadata: Mapping[str, object],
) -> PreparedStateManifest:
    for value, label in (
        (producer_sha256, "producer_sha256"),
        (output_plan_sha256, "output_plan_sha256"),
        (state_fingerprint, "state_fingerprint"),
    ):
        digest(value, label)
    parsed_metadata = portable_json(metadata, "prepared state metadata")
    if not isinstance(parsed_metadata, dict):
        raise TypeError("prepared state metadata must be an object")
    manifest_path = staging / PREPARED_STATE_MANIFEST
    if manifest_path.exists() or manifest_path.is_symlink():
        raise FileExistsError(f"prepared state manifest already exists: {manifest_path}")
    payload = _payload_manifest(staging)
    value: JsonObject = {
        "schema": "marimo-export.prepared-state.v1",
        "producer_sha256": producer_sha256,
        "output_plan_sha256": output_plan_sha256,
        "state_fingerprint": state_fingerprint,
        "metadata": cast(JsonValue, parsed_metadata),
        "files": cast(JsonValue, payload),
    }
    encoded = canonical_bytes(value)
    _write_file(manifest_path, encoded)
    sync_tree(staging)
    instance = sha256(encoded).hexdigest()
    closure = verified_tree(staging)
    return PreparedStateManifest(
        producer_sha256,
        output_plan_sha256,
        state_fingerprint,
        instance,
        parsed_metadata,
        closure,
    )


def verify_prepared_state(path: Path) -> PreparedStateManifest:
    manifest_path = path / PREPARED_STATE_MANIFEST
    inspected_root = _inspect_path(path)
    if stat.S_ISLNK(inspected_root.st_mode) or not stat.S_ISDIR(inspected_root.st_mode):
        raise RepositoryIntegrityError("The prepared state root is invalid.")
    inspected_manifest = _inspect_path(manifest_path)
    if stat.S_ISLNK(inspected_manifest.st_mode) or not stat.S_ISREG(inspected_manifest.st_mode):
        raise RepositoryIntegrityError("The prepared state manifest is missing.")
    try:
        encoded = manifest_path.read_bytes()
    except (OSError, MemoryError) as error:
        _raise_artifact_path_error(error, manifest_path)
    try:
        raw = parse_canonical_json(encoded, "prepared state manifest")
    except (TypeError, ValueError) as error:
        raise RepositoryIntegrityError("The prepared state manifest is invalid.") from error
    if not isinstance(raw, dict) or set(raw) != {
        "schema",
        "producer_sha256",
        "output_plan_sha256",
        "state_fingerprint",
        "metadata",
        "files",
    }:
        raise RepositoryIntegrityError("The prepared state manifest fields are invalid.")
    if raw["schema"] != "marimo-export.prepared-state.v1":
        raise RepositoryIntegrityError("The prepared state manifest schema is invalid.")
    try:
        producer = digest(raw["producer_sha256"], "producer_sha256")
        output_plan = digest(raw["output_plan_sha256"], "output_plan_sha256")
        state = digest(raw["state_fingerprint"], "state_fingerprint")
    except ValueError as error:
        raise RepositoryIntegrityError("The prepared state identity is invalid.") from error
    metadata = raw["metadata"]
    files = raw["files"]
    if not isinstance(metadata, dict) or not isinstance(files, dict):
        raise RepositoryIntegrityError("The prepared state manifest content is invalid.")
    expected = _parse_payload_manifest(cast(dict[str, JsonValue], files))
    actual = _payload_manifest(path)
    if expected != actual:
        raise RepositoryIntegrityError("The prepared state file closure is invalid.")
    closure = verified_tree(path)
    return PreparedStateManifest(
        producer,
        output_plan,
        state,
        sha256(encoded).hexdigest(),
        cast(JsonObject, metadata),
        closure,
    )


def verify_export(path: Path) -> tuple[str, VerifiedClosure]:
    notebook_export = open_export(path)
    notebook_export.verify()
    closure = verified_export_tree(path)
    return notebook_export.identity, closure


def verified_export_tree(root: Path) -> VerifiedClosure:
    inspected_root = _inspect_path(root)
    if stat.S_ISLNK(inspected_root.st_mode) or not stat.S_ISDIR(inspected_root.st_mode):
        raise RepositoryIntegrityError("The export generation root is invalid.")
    allowed = {"index.json", "assets"}
    if any(entry.name not in allowed for entry in _directory_entries(root)):
        raise RepositoryIntegrityError("The export generation contains an undeclared root entry.")
    index = root / "index.json"
    inspected_index = _inspect_path(index)
    if stat.S_ISLNK(inspected_index.st_mode) or not stat.S_ISREG(inspected_index.st_mode):
        raise RepositoryIntegrityError("The export generation has no regular index.json.")
    return verified_tree(root)


def verified_tree(root: Path) -> VerifiedClosure:
    inspected_root = _inspect_path(root)
    if stat.S_ISLNK(inspected_root.st_mode) or not stat.S_ISDIR(inspected_root.st_mode):
        raise RepositoryIntegrityError("The repository artifact root is invalid.")
    files: set[str] = set()
    content_bytes = 0
    for path, inspected in _tree_entries(root):
        if path == root:
            continue
        if stat.S_ISLNK(inspected.st_mode):
            raise RepositoryIntegrityError("The repository artifact contains a symlink.")
        if stat.S_ISDIR(inspected.st_mode):
            continue
        if not stat.S_ISREG(inspected.st_mode):
            raise RepositoryIntegrityError("The repository artifact contains a non-regular file.")
        relative = path.relative_to(root).as_posix()
        files.add(relative)
        content_bytes += inspected.st_size
    return VerifiedClosure(frozenset(files), content_bytes)


def atomic_install(
    staging: Path,
    target: Path,
    instance: str,
    verifier: Callable[[Path], tuple[str, VerifiedClosure]],
) -> VerifiedClosure:
    digest(instance, "artifact instance")
    private_directory(target.parent)
    if target.is_symlink():
        target.unlink()
        sync_directory(target.parent)
    elif target.exists():
        try:
            existing_instance, closure = verifier(target)
            if existing_instance == instance:
                shutil.rmtree(staging)
                return closure
        except (NotebookExportError, RepositoryIntegrityError):
            pass
        backup = target.parent / f".{instance}-incomplete-{time.time_ns()}"
        os.replace(target, backup)
        sync_directory(target.parent)
        try:
            os.replace(staging, target)
            sync_directory(target.parent)
            closure = verifier(target)[1]
            protect_and_sync_tree(target)
            make_tree_writable(backup)
            shutil.rmtree(backup)
            sync_directory(target.parent)
            return closure
        except BaseException as error:
            _restore_failed_install(staging, target, backup, error)
            raise
    os.replace(staging, target)
    sync_directory(target.parent)
    try:
        closure = verifier(target)[1]
        protect_and_sync_tree(target)
        return closure
    except BaseException as error:
        _restore_failed_install(staging, target, None, error)
        raise


def _restore_failed_install(
    staging: Path,
    target: Path,
    backup: Path | None,
    primary: BaseException,
) -> None:
    try:
        if (target.exists() or target.is_symlink()) and not staging.exists():
            os.replace(target, staging)
        if backup is not None and (backup.exists() or backup.is_symlink()):
            os.replace(backup, target)
        sync_directory(target.parent)
    except BaseException as cleanup_error:
        record_cleanup_failure(
            primary,
            "artifact install rollback",
            cleanup_error,
        )


def quarantine(path: Path) -> QuarantinedArtifact | None:
    if not path.exists() and not path.is_symlink():
        return None
    target = path.parent / f".{path.name}-quarantine-{uuid4().hex}"
    os.replace(path, target)
    sync_directory(path.parent)
    return QuarantinedArtifact(path, target)


def restore_quarantine(item: QuarantinedArtifact) -> None:
    if not item.quarantine.exists() and not item.quarantine.is_symlink():
        return
    if item.original.exists() or item.original.is_symlink():
        remove_tree(item.quarantine)
    else:
        os.replace(item.quarantine, item.original)
    sync_directory(item.original.parent)


def discard_quarantine(item: QuarantinedArtifact) -> None:
    if item.quarantine.exists() or item.quarantine.is_symlink():
        if item.quarantine.is_symlink() or item.quarantine.is_file():
            item.quarantine.unlink()
        else:
            make_tree_writable(item.quarantine)
            shutil.rmtree(item.quarantine)
        if item.quarantine.exists() or item.quarantine.is_symlink():
            raise OSError(f"Retired repository artifact could not be removed: {item.quarantine}")
        sync_directory(item.quarantine.parent)


def discard_staging(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ValueError("staging directory belongs to another repository") from error
    if relative.parts[:1] != ("staging",) or not path.name.startswith("stage-"):
        raise ValueError("staging path is invalid")
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)
    if path.exists() or path.is_symlink():
        raise OSError(f"Repository staging could not be removed: {path}")
    return relative.as_posix()


def remove_tree(path: Path) -> None:
    if path.is_symlink():
        path.unlink(missing_ok=True)
        return
    if path.exists():
        make_tree_writable(path)
    shutil.rmtree(path, ignore_errors=True)


def protect_tree(root: Path) -> None:
    for path, inspected in _tree_entries(root):
        if stat.S_ISLNK(inspected.st_mode):
            raise OSError(f"Repository artifact contains a symlink: {path}")
        try:
            os.chmod(
                path,
                stat.S_IRWXU if stat.S_ISDIR(inspected.st_mode) else stat.S_IRUSR,
            )
        except OSError:
            if os.name != "nt":
                raise


def protect_and_sync_tree(root: Path) -> None:
    if os.name == "nt":
        sync_tree(root)
        protect_tree(root)
        return
    protect_tree(root)
    sync_tree(root)


def make_tree_writable(root: Path) -> None:
    try:
        inspected_root = root.lstat()
    except FileNotFoundError:
        return
    except (OSError, MemoryError) as error:
        _raise_artifact_path_error(error, root)
    if stat.S_ISLNK(inspected_root.st_mode):
        return
    for path, inspected in _tree_entries(root):
        if stat.S_ISLNK(inspected.st_mode):
            continue
        try:
            os.chmod(
                path,
                stat.S_IRWXU if stat.S_ISDIR(inspected.st_mode) else stat.S_IRUSR | stat.S_IWUSR,
            )
        except OSError:
            if os.name != "nt":
                raise


def sync_tree(root: Path) -> None:
    directories: list[Path] = []
    for path, inspected in _tree_entries(root):
        if stat.S_ISLNK(inspected.st_mode):
            raise OSError(f"Repository artifact contains a symlink: {path}")
        if stat.S_ISDIR(inspected.st_mode):
            directories.append(path)
            continue
        if not stat.S_ISREG(inspected.st_mode):
            raise OSError(f"Repository artifact contains a non-regular file: {path}")
        descriptor = os.open(path, os.O_WRONLY if os.name == "nt" else os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    for directory in reversed(directories):
        sync_directory(directory)


def safe_member(root: Path, files: frozenset[str], relative: str) -> Path | None:
    normalized = PurePosixPath(relative)
    if normalized.is_absolute() or ".." in normalized.parts or normalized.as_posix() not in files:
        return None
    if root.is_symlink() or not root.is_dir():
        return None
    candidate = root.joinpath(*normalized.parts)
    current = candidate
    while current != root:
        if current.is_symlink():
            return None
        current = current.parent
    return candidate if candidate.is_file() else None


def tree_bytes(root: Path) -> int:
    if not root.exists() or root.is_symlink():
        return 0
    return verified_tree(root).content_bytes


def physical_tree_bytes(root: Path, *, limit: int) -> int:
    """Count a tree without following links and stop after the declared bound."""

    total = 0
    try:
        root.lstat()
    except FileNotFoundError:
        return 0
    except (OSError, MemoryError) as error:
        _raise_artifact_path_error(error, root)
    for path, inspected in _tree_entries(root):
        if path == root:
            continue
        total += inspected.st_size
        if total > limit:
            return total
    return total


def _payload_manifest(root: Path) -> JsonObject:
    result: JsonObject = {}
    for path, inspected in _tree_entries(root):
        if path == root or stat.S_ISDIR(inspected.st_mode):
            continue
        relative = path.relative_to(root).as_posix()
        if relative == PREPARED_STATE_MANIFEST:
            continue
        if stat.S_ISLNK(inspected.st_mode):
            raise RepositoryIntegrityError("The prepared state contains a symlink.")
        if not stat.S_ISREG(inspected.st_mode):
            raise RepositoryIntegrityError("The prepared state contains a non-regular file.")
        try:
            payload = path.read_bytes()
        except (OSError, MemoryError) as error:
            _raise_artifact_path_error(error, path)
        result[relative] = {
            "sha256": sha256(payload).hexdigest(),
            "size": len(payload),
        }
    return result


def _parse_payload_manifest(value: dict[str, JsonValue]) -> JsonObject:
    parsed: JsonObject = {}
    for relative, details in value.items():
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or path.as_posix() != relative:
            raise RepositoryIntegrityError("The prepared state file path is invalid.")
        if not isinstance(details, dict) or set(details) != {"sha256", "size"}:
            raise RepositoryIntegrityError("The prepared state file record is invalid.")
        file_digest = details["sha256"]
        size = details["size"]
        try:
            digest(file_digest, "prepared state file digest")
        except ValueError as error:
            raise RepositoryIntegrityError("The prepared state file digest is invalid.") from error
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise RepositoryIntegrityError("The prepared state file size is invalid.")
        parsed[relative] = {"sha256": file_digest, "size": size}
    return parsed


def _write_file(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("repository file write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def walk_tree(
    root: Path,
    *,
    topdown: bool = True,
) -> Iterator[tuple[str, list[str], list[str]]]:
    def unavailable(error: OSError) -> None:
        _raise_artifact_path_error(error, Path(error.filename or root))

    yield from os.walk(
        root,
        topdown=topdown,
        onerror=unavailable,
        followlinks=False,
    )


def _tree_entries(root: Path) -> Iterator[tuple[Path, os.stat_result]]:
    inspected_root = _inspect_path(root)
    yield root, inspected_root
    if not stat.S_ISDIR(inspected_root.st_mode) or stat.S_ISLNK(inspected_root.st_mode):
        return
    pending = [root]
    while pending:
        directory = pending.pop()
        for entry in _directory_entries(directory):
            path = Path(entry.path)
            try:
                inspected = entry.stat(follow_symlinks=False)
            except (OSError, MemoryError) as error:
                _raise_artifact_path_error(error, path)
            yield path, inspected
            if stat.S_ISDIR(inspected.st_mode) and not stat.S_ISLNK(inspected.st_mode):
                pending.append(path)


def _directory_entries(directory: Path) -> tuple[os.DirEntry[str], ...]:
    try:
        with os.scandir(directory) as entries:
            return tuple(entries)
    except (OSError, MemoryError) as error:
        _raise_artifact_path_error(error, directory)


def _inspect_path(path: Path) -> os.stat_result:
    try:
        return path.lstat()
    except (OSError, MemoryError) as error:
        _raise_artifact_path_error(error, path)


def _raise_artifact_path_error(
    error: OSError | MemoryError,
    path: Path,
) -> NoReturn:
    if isinstance(error, OSError) and error.errno in {
        errno.ENOENT,
        errno.ENOTDIR,
        errno.EISDIR,
        errno.ELOOP,
    }:
        raise RepositoryIntegrityError("The repository artifact structure is invalid.") from error
    raise ExportUnavailableError(
        "repository artifact storage is unavailable",
        details={"path": str(path)},
    ) from error


__all__ = [
    "PREPARED_STATE_MANIFEST",
    "PreparedStateManifest",
    "QuarantinedArtifact",
    "VerifiedClosure",
    "atomic_install",
    "discard_quarantine",
    "discard_staging",
    "make_tree_writable",
    "physical_tree_bytes",
    "protect_and_sync_tree",
    "protect_tree",
    "quarantine",
    "remove_tree",
    "restore_quarantine",
    "safe_member",
    "sync_tree",
    "tree_bytes",
    "verified_export_tree",
    "verified_tree",
    "verify_export",
    "verify_prepared_state",
    "write_prepared_state_manifest",
]
