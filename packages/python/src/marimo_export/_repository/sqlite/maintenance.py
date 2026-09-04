from __future__ import annotations

import os
import sqlite3
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from uuid import uuid4

from marimo_export._repository.models import (
    RepositoryBusyError,
    RepositoryUnavailableError,
)
from marimo_export._repository.paths import sync_directory


class _InvalidMaintenanceSchema(RuntimeError):
    pass


@contextmanager
def maintenance_lock(root: Path, *, timeout_seconds: float = 10.0) -> Iterator[None]:
    """Serialize artifact filesystem mutations without blocking catalog heartbeats."""

    path = root / "maintenance.sqlite3"
    connection = _connect(path, timeout_seconds=timeout_seconds)
    begun = False
    try:
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            if any(term in str(error).lower() for term in ("locked", "busy")):
                raise RepositoryBusyError(
                    "The export repository maintenance lock remained busy."
                ) from error
            raise RepositoryUnavailableError(
                "The export repository maintenance lock is unavailable."
            ) from error
        begun = True
        yield
        connection.commit()
        begun = False
    finally:
        if begun:
            connection.rollback()
        connection.close()
        if os.name != "nt" and path.is_file() and not path.is_symlink():
            os.chmod(path, 0o600)


def _connect(path: Path, *, timeout_seconds: float) -> sqlite3.Connection:
    deadline = time.monotonic() + max(0.001, timeout_seconds)
    _cleanup_retired_locks(path)
    schema_attempt = 0
    while schema_attempt < 2:
        before = _lock_stat(path)
        bounded = max(0.001, deadline - time.monotonic())
        connection = sqlite3.connect(path, timeout=bounded, isolation_level=None)
        try:
            connection.execute(f"PRAGMA busy_timeout = {int(bounded * 1000)}")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS maintenance_lock (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1)
                )
                """
            )
            columns = tuple(
                str(row[1]) for row in connection.execute('PRAGMA table_info("maintenance_lock")')
            )
            if columns != ("singleton",):
                raise _InvalidMaintenanceSchema
            connection.execute("INSERT OR IGNORE INTO maintenance_lock(singleton) VALUES (1)")
            return connection
        except _InvalidMaintenanceSchema:
            connection.close()
            after = _lock_stat(path)
            if before is not None and after is not None and os.path.samestat(before, after):
                _discard_corrupt_lock(path, expected=before, deadline=deadline)
            schema_attempt += 1
        except sqlite3.DatabaseError as error:
            connection.close()
            after = _lock_stat(path)
            if before is None or after is None or not os.path.samestat(before, after):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RepositoryUnavailableError(
                        "The export repository maintenance lock is unavailable."
                    ) from error
                time.sleep(min(0.01, remaining))
                continue
            message = str(error).lower()
            if any(term in message for term in ("locked", "busy")):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RepositoryBusyError(
                        "The export repository maintenance lock remained busy."
                    ) from error
                time.sleep(min(0.01, remaining))
                continue
            if "malformed" not in message and "not a database" not in message:
                raise RepositoryUnavailableError(
                    "The export repository maintenance lock is unavailable."
                ) from error
            if before is not None and after is not None and os.path.samestat(before, after):
                _discard_corrupt_lock(path, expected=before, deadline=deadline)
            schema_attempt += 1
    raise RepositoryUnavailableError("The export repository maintenance lock is unavailable.")


def _lock_stat(path: Path) -> os.stat_result | None:
    try:
        inspected = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(inspected.st_mode):
        raise OSError(f"Repository maintenance lock is a symlink: {path}")
    return inspected


def _discard_corrupt_lock(
    path: Path,
    *,
    expected: os.stat_result,
    deadline: float,
) -> None:
    token = uuid4().hex
    candidates: list[tuple[Path, os.stat_result]] = []
    for suffix in ("", "-wal", "-shm"):
        source = Path(f"{path}{suffix}")
        try:
            inspected = source.stat()
        except FileNotFoundError:
            continue
        if not suffix and not os.path.samestat(expected, inspected):
            return
        candidates.append((source, inspected))
    for source, inspected in candidates:
        target = path.parent / f".{source.name}.corrupt-{token}"
        while True:
            try:
                current = source.stat()
            except FileNotFoundError:
                if source == path:
                    return
                break
            if not os.path.samestat(inspected, current):
                if source == path:
                    return
                break
            try:
                os.replace(source, target)
            except FileNotFoundError:
                if source == path:
                    return
                break
            except PermissionError as error:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RepositoryBusyError(
                        "The export repository maintenance lock remained busy during recovery."
                    ) from error
                time.sleep(min(0.01, remaining))
                continue
            break
    sync_directory(path.parent)
    _cleanup_retired_locks(path)
    sync_directory(path.parent)


def _cleanup_retired_locks(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        for target in path.parent.glob(f".{path.name}{suffix}.corrupt-*"):
            with suppress(PermissionError):
                target.unlink(missing_ok=True)


__all__ = ["maintenance_lock"]
