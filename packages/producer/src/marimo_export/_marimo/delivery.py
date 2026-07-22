from __future__ import annotations

import errno
import hashlib
import os
import re
import shutil
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from marimo_export._json import JsonObject
from marimo_export._marimo.cache import read_key, read_payload, validate_key
from marimo_export._marimo.context import notebook_path
from marimo_export.errors import IntegrityError
from marimo_export.index import ExportIndex, ExportRef

_STAGE_TTL_SECONDS = 30 * 60
_CLEANUP_RETRY_SECONDS = 5
_STAGE_ID = re.compile(r"[0-9a-f]{32}")


@dataclass
class _Lease:
    expires_at_ms: int | None = None
    cleanup_at: float | None = None


_LEASE_LOCK = threading.RLock()
_LEASES: dict[tuple[Path, str], _Lease] = {}
_ROOT_LOCKS_GUARD = threading.Lock()
_ROOT_LOCKS: dict[Path, threading.RLock] = {}
_SCHEDULER_TIMER: threading.Timer | None = None
_SCHEDULER_TOKEN: object | None = None


def stage(ref: ExportRef) -> JsonObject:
    path = notebook_path()
    index_bytes = _read_index(ref)
    try:
        index = ExportIndex.from_bytes(index_bytes)
    except (TypeError, ValueError) as error:
        raise IntegrityError(str(error)) from error

    root = path.parent / "public" / ".marimo-export"
    _ensure_root(root)
    stage_id = uuid.uuid4().hex
    lease = _Lease()
    target = root / stage_id
    temporary = root / f".{stage_id}.tmp"
    with _locked_root(root):
        with _LEASE_LOCK:
            _collect_orphans_locked(root)
            _LEASES[(root, stage_id)] = lease
            _schedule_locked()
        try:
            temporary.mkdir()
            for payload in index.payloads():
                data = read_payload(payload.key, payload.sha256, payload.size)
                _atomic_write(temporary / "cache" / validate_key(payload.key), data)
            _atomic_write(temporary / "index.json", index_bytes)
            with _LEASE_LOCK:
                if _LEASES.get((root, stage_id)) is not lease:
                    raise RuntimeError("stage was released before publication completed")
                published_at = time.time()
                os.utime(temporary, (published_at, published_at))
                os.replace(temporary, target)
                # A restarted producer restores the remaining lease duration from
                # the publication timestamp on the served directory.
                lease.expires_at_ms = int((published_at + _STAGE_TTL_SECONDS) * 1000)
                lease.cleanup_at = lease.expires_at_ms / 1000
                _schedule_locked()
        except BaseException:
            with _LEASE_LOCK:
                if _LEASES.get((root, stage_id)) is lease:
                    _LEASES.pop((root, stage_id))
                    _schedule_locked()
                _remove_directory_locked(temporary)
                _remove_directory_locked(target)
            raise
    if lease.expires_at_ms is None:
        raise RuntimeError("stage expiry was not initialized")
    return {
        "expires_at_ms": lease.expires_at_ms,
        "id": stage_id,
        "url": f"./public/.marimo-export/{stage_id}/",
        "notebook_key": str(path),
    }


def release(stage_id: str) -> bool:
    if _STAGE_ID.fullmatch(stage_id) is None:
        raise ValueError("stage id must be 32 lowercase hexadecimal characters")
    root = notebook_path().parent / "public" / ".marimo-export"
    if root.is_symlink():
        raise RuntimeError(f"stage root must be a directory, got symlink: {root}")
    if not root.exists():
        return False
    _ensure_root(root)
    with _locked_root(root), _LEASE_LOCK:
        lease = _LEASES.get((root, stage_id))
        removed = _remove_directory_locked(root / stage_id)
        removed_temporary = _remove_directory_locked(root / f".{stage_id}.tmp")
        if lease is not None:
            _LEASES.pop((root, stage_id))
        _collect_orphans_locked(root)
        _schedule_locked()
        return lease is not None or removed or removed_temporary


def _read_index(ref: ExportRef) -> bytes:
    data = read_key(ref.key)
    if len(data) != ref.size or hashlib.sha256(data).hexdigest() != ref.sha256:
        raise IntegrityError("export index failed integrity verification")
    return data


def _atomic_write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _schedule_locked() -> None:
    global _SCHEDULER_TIMER, _SCHEDULER_TOKEN

    if _SCHEDULER_TIMER is not None:
        _SCHEDULER_TIMER.cancel()
    deadlines = [lease.cleanup_at for lease in _LEASES.values() if lease.cleanup_at is not None]
    if not deadlines:
        _SCHEDULER_TIMER = None
        _SCHEDULER_TOKEN = None
        return
    token = object()
    timer = threading.Timer(max(0, min(deadlines) - time.time()), _run_scheduler, args=(token,))
    timer.daemon = True
    _SCHEDULER_TOKEN = token
    _SCHEDULER_TIMER = timer
    timer.start()


def _run_scheduler(token: object) -> None:
    global _SCHEDULER_TIMER, _SCHEDULER_TOKEN

    with _LEASE_LOCK:
        if _SCHEDULER_TOKEN is not token:
            return
        _SCHEDULER_TIMER = None
        _SCHEDULER_TOKEN = None
        now = time.time()
        due = [
            (root, stage_id, lease)
            for (root, stage_id), lease in _LEASES.items()
            if lease.cleanup_at is not None and lease.cleanup_at <= now
        ]
    for root, stage_id, lease in due:
        try:
            with _locked_root(root), _LEASE_LOCK:
                current = _LEASES.get((root, stage_id))
                if current is not lease:
                    continue
                if lease.cleanup_at is None or lease.cleanup_at > time.time():
                    continue
                try:
                    _remove_directory_locked(root / stage_id)
                    _remove_directory_locked(root / f".{stage_id}.tmp")
                except OSError:
                    lease.cleanup_at = time.time() + _CLEANUP_RETRY_SECONDS
                    continue
                _LEASES.pop((root, stage_id))
        except (OSError, RuntimeError):
            with _LEASE_LOCK:
                if _LEASES.get((root, stage_id)) is lease:
                    lease.cleanup_at = time.time() + _CLEANUP_RETRY_SECONDS
    with _LEASE_LOCK:
        _schedule_locked()


def _collect_orphans_locked(root: Path) -> None:
    now = time.time()
    try:
        children = list(root.iterdir())
    except FileNotFoundError:
        return
    for child in children:
        name = child.name
        is_temporary = name.startswith(".") and name.endswith(".tmp")
        stage_name = name[1:-4] if is_temporary else name
        if _STAGE_ID.fullmatch(stage_name) is None or child.is_symlink() or not child.is_dir():
            continue
        if (root, stage_name) in _LEASES:
            continue
        if is_temporary:
            try:
                _remove_directory_locked(child)
            except OSError:
                _LEASES[(root, stage_name)] = _Lease(cleanup_at=now + _CLEANUP_RETRY_SECONDS)
            continue
        try:
            remaining = child.stat().st_mtime + _STAGE_TTL_SECONDS - now
        except FileNotFoundError:
            continue
        if remaining <= 0:
            try:
                _remove_directory_locked(child)
            except OSError:
                lease = _Lease(cleanup_at=now + _CLEANUP_RETRY_SECONDS)
                _LEASES[(root, stage_name)] = lease
            continue
        expires_at_ms = int((now + remaining) * 1000)
        lease = _Lease(expires_at_ms=expires_at_ms, cleanup_at=expires_at_ms / 1000)
        _LEASES[(root, stage_name)] = lease


def _ensure_root(root: Path) -> None:
    if root.is_symlink():
        raise RuntimeError(f"stage root must be a directory, got symlink: {root}")
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError(f"stage root must be a directory: {root}")


@contextmanager
def _locked_root(root: Path) -> Iterator[None]:
    _ensure_root(root)
    with _ROOT_LOCKS_GUARD:
        local_lock = _ROOT_LOCKS.setdefault(root, threading.RLock())
    with local_lock:
        lock_path = root / ".lock"
        with lock_path.open("a+b") as lock_file:
            _lock_file(lock_file)
            try:
                yield
            finally:
                _unlock_file(lock_file)


def _lock_file(lock_file: BinaryIO) -> None:
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        if lock_file.read(1) == b"":
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        while True:
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                time.sleep(0.05)
    else:
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)


def _unlock_file(lock_file: BinaryIO) -> None:
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _remove_directory_locked(path: Path) -> bool:
    if path.is_symlink() or not path.is_dir():
        return False
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        return False
    return True
