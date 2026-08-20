from __future__ import annotations

import sqlite3
from collections.abc import Mapping

from marimo_export._repository.sqlite import leases
from marimo_export._repository.sqlite.records import integer


def repository_status(connection: sqlite3.Connection, now_us: int) -> Mapping[str, int]:
    leases.delete_expired(connection, now_us)
    counts = {
        "producers": "SELECT COUNT(*) FROM producers",
        "observations": "SELECT COUNT(*) FROM observations",
        "prepared_states": "SELECT COUNT(*) FROM prepared_states",
        "identities": "SELECT COUNT(*) FROM identities",
        "generations": "SELECT COUNT(*) FROM generations",
        "active_leases": "SELECT COUNT(*) FROM artifact_leases",
    }
    result = {
        name: integer(connection.execute(query).fetchone()[0]) for name, query in counts.items()
    }
    row = connection.execute(
        """
        SELECT
            COALESCE((SELECT SUM(content_bytes) FROM prepared_states), 0) +
            COALESCE((SELECT SUM(content_bytes) FROM generations), 0) +
            COALESCE((SELECT SUM(content_bytes) FROM retired_artifacts), 0)
        """
    ).fetchone()
    result["content_bytes"] = integer(row[0]) if row is not None else 0
    return result


__all__ = ["repository_status"]
