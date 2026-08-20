from __future__ import annotations

import sqlite3

from marimo_export._repository.sqlite.records import integer


def all_rows(connection: sqlite3.Connection) -> tuple[tuple[str, int | None], ...]:
    result: list[tuple[str, int | None]] = []
    invalid_rowids: list[int] = []
    for row in connection.execute(
        """
        SELECT rowid, relative_path, content_bytes FROM retired_artifacts
        ORDER BY created_at_us, relative_path
        """
    ):
        if not isinstance(row[1], str):
            invalid_rowids.append(integer(row[0]))
            continue
        try:
            content_bytes = integer(row[2])
            if content_bytes < 0:
                raise ValueError("content bytes")
        except (TypeError, ValueError):
            content_bytes = None
        result.append((row[1], content_bytes))
    connection.executemany(
        "DELETE FROM retired_artifacts WHERE rowid = ?",
        ((rowid,) for rowid in invalid_rowids),
    )
    return tuple(result)


def record(
    connection: sqlite3.Connection,
    relative_path: str,
    content_bytes: int,
    now_us: int,
) -> None:
    connection.execute(
        """
        INSERT INTO retired_artifacts(relative_path, content_bytes, created_at_us)
        VALUES (?, ?, ?)
        ON CONFLICT(relative_path) DO UPDATE SET
            content_bytes = excluded.content_bytes
        """,
        (relative_path, content_bytes, now_us),
    )


def release(connection: sqlite3.Connection, relative_path: str) -> None:
    connection.execute(
        "DELETE FROM retired_artifacts WHERE relative_path = ?",
        (relative_path,),
    )


__all__ = ["all_rows", "record", "release"]
