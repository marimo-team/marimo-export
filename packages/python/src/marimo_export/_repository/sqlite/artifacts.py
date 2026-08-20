from __future__ import annotations

import sqlite3

from marimo_export._repository.models import (
    RepositoryFenceError,
    RepositoryIdentity,
    RepositoryLimitError,
    RepositoryLimits,
)
from marimo_export._repository.sqlite.records import integer


def touch_producer(
    connection: sqlite3.Connection,
    producer_sha256: str,
    now_us: int,
) -> None:
    connection.execute(
        """
        INSERT INTO producers(producer_sha256, touched_at_us)
        VALUES (?, ?)
        ON CONFLICT(producer_sha256) DO UPDATE SET touched_at_us = excluded.touched_at_us
        """,
        (producer_sha256, now_us),
    )


def upsert_identity(
    connection: sqlite3.Connection,
    identity: RepositoryIdentity,
    now_us: int,
) -> None:
    touch_producer(connection, identity.producer_sha256, now_us)
    connection.execute(
        """
        INSERT INTO identities(
            identity_key, producer_sha256, output_plan_sha256,
            spec_sha256, touched_at_us
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(identity_key) DO UPDATE SET touched_at_us = excluded.touched_at_us
        """,
        (
            identity.key,
            identity.producer_sha256,
            identity.output_plan_sha256,
            identity.spec_sha256,
            now_us,
        ),
    )


def require_replacement(
    connection: sqlite3.Connection,
    *,
    table: str,
    key_column: str,
    key: str,
    replacing_instance: str | None,
    candidate_instance: str,
    label: str,
) -> None:
    row = connection.execute(
        f"SELECT current_instance FROM {table} WHERE {key_column} = ?",
        (key,),
    ).fetchone()
    current = str(row[0]) if row is not None and row[0] is not None else None
    if replacing_instance is None:
        if current is not None and current != candidate_instance:
            raise RepositoryFenceError(f"the initial {label} pointer is no longer empty")
        return
    if current != replacing_instance:
        raise RepositoryFenceError(f"the replaced {label} is no longer current")


def admit_artifact(
    connection: sqlite3.Connection,
    *,
    kind: str,
    content_bytes: int,
    metadata_bytes: int,
    exists: bool,
    credit_content_bytes: int,
    credit_metadata_bytes: int,
    limits: RepositoryLimits,
) -> None:
    if exists:
        return
    table = "prepared_states" if kind == "state" else "generations"
    category_limit = limits.prepared_state_bytes if kind == "state" else limits.generation_bytes
    category = connection.execute(f"SELECT COALESCE(SUM(content_bytes), 0) FROM {table}").fetchone()
    category_bytes = integer(category[0]) if category is not None else 0
    if category_bytes + content_bytes - credit_content_bytes > category_limit:
        raise RepositoryLimitError(f"The repository {kind} byte limit is exhausted.")
    total = connection.execute(
        """
        SELECT
            COALESCE((SELECT SUM(content_bytes) FROM prepared_states), 0) +
            COALESCE((SELECT SUM(content_bytes) FROM generations), 0) +
            COALESCE((SELECT SUM(content_bytes) FROM retired_artifacts), 0)
        """
    ).fetchone()
    total_bytes = integer(total[0]) if total is not None else 0
    if total_bytes + content_bytes - credit_content_bytes > limits.repository_bytes:
        raise RepositoryLimitError("The export repository byte limit is exhausted.")
    metadata = connection.execute(
        """
        SELECT
            COALESCE((SELECT SUM(metadata_bytes) FROM prepared_states), 0) +
            COALESCE((SELECT SUM(metadata_bytes) FROM generations), 0)
        """
    ).fetchone()
    total_metadata = integer(metadata[0]) if metadata is not None else 0
    if total_metadata + metadata_bytes - credit_metadata_bytes > limits.metadata_bytes:
        raise RepositoryLimitError("The export repository metadata limit is exhausted.")


def current_artifacts(
    connection: sqlite3.Connection,
) -> tuple[frozenset[tuple[str, str]], frozenset[tuple[str, str]]]:
    states = frozenset(
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            """
            SELECT state_key, current_instance FROM state_scopes
            WHERE current_instance IS NOT NULL
            """
        )
    )
    generations = frozenset(
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            """
            SELECT identity_key, current_instance FROM identities
            WHERE current_instance IS NOT NULL
            """
        )
    )
    return states, generations


__all__ = [
    "admit_artifact",
    "current_artifacts",
    "require_replacement",
    "touch_producer",
    "upsert_identity",
]
