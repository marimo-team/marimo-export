from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from marimo_export._repository.paths import sync_directory
from marimo_export._repository.sqlite.catalog import SqliteCatalog, operational_error
from marimo_export._repository.sqlite.schema import IncompatibleRepositorySchema


def open_catalog(root: Path) -> tuple[SqliteCatalog, tuple[Path, ...]]:
    path = root / "catalog.sqlite3"
    try:
        return SqliteCatalog(path), ()
    except IncompatibleRepositorySchema:
        quarantined = _quarantine_database(path, label="incompatible")
        return SqliteCatalog(path), quarantined
    except sqlite3.OperationalError as error:
        raise operational_error(error) from error
    except sqlite3.DatabaseError as error:
        if not _confirmed_corruption(path, error):
            raise
        quarantined = _quarantine_database(path, label="corrupt")
        return SqliteCatalog(path), quarantined


def _confirmed_corruption(path: Path, original: sqlite3.DatabaseError) -> bool:
    if not _corruption_error(original) or path.is_symlink() or not path.is_file():
        return False
    try:
        connection = sqlite3.connect(
            f"file:{path}?mode=ro",
            uri=True,
            timeout=0.25,
            isolation_level=None,
        )
        try:
            connection.execute("PRAGMA busy_timeout = 250")
            row = connection.execute("PRAGMA quick_check(1)").fetchone()
            return row is None or row != ("ok",)
        finally:
            connection.close()
    except sqlite3.DatabaseError as error:
        return _corruption_error(error)


def _corruption_error(error: sqlite3.DatabaseError) -> bool:
    message = str(error).lower()
    return "malformed" in message or "not a database" in message


def _quarantine_database(path: Path, *, label: str) -> tuple[Path, ...]:
    token = uuid4().hex
    quarantined: list[Path] = []
    for suffix in ("", "-wal", "-shm"):
        source = Path(f"{path}{suffix}")
        if not source.exists() and not source.is_symlink():
            continue
        target = path.parent / f".{source.name}.{label}-{token}"
        os.replace(source, target)
        quarantined.append(target)
    sync_directory(path.parent)
    return tuple(quarantined)


__all__ = ["open_catalog"]
