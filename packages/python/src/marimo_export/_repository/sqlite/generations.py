from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from marimo_export._repository.models import (
    RepositoryFenceError,
    RepositoryIdentity,
    RepositoryLimits,
)
from marimo_export._repository.sqlite import artifacts, leases
from marimo_export._repository.sqlite.observations import observation_revision
from marimo_export._repository.sqlite.records import (
    GenerationRow,
    StateRow,
    generation_row,
    integer,
)


def check_commit(
    connection: sqlite3.Connection,
    *,
    identity: RepositoryIdentity,
    instance: str,
    replacing_instance: str | None,
    reservation_owner: str,
    reservation_identity_key: str,
    reservation_fence: int,
    now_us: int,
) -> None:
    if reservation_identity_key != identity.key:
        raise RepositoryFenceError(
            "The preparation reservation belongs to another export identity."
        )
    leases.require_reservation(
        connection,
        owner=reservation_owner,
        identity_key=reservation_identity_key,
        fence=reservation_fence,
        producer_sha256=identity.producer_sha256,
        output_plan_sha256=identity.output_plan_sha256,
        spec_sha256=identity.spec_sha256,
        now_us=now_us,
    )
    artifacts.require_replacement(
        connection,
        table="identities",
        key_column="identity_key",
        key=identity.key,
        replacing_instance=replacing_instance,
        candidate_instance=instance,
        label="export generation",
    )


def commit(
    connection: sqlite3.Connection,
    *,
    identity: RepositoryIdentity,
    instance: str,
    metadata: bytes,
    captured_observation_revision: int,
    content_bytes: int,
    states: Sequence[StateRow],
    replacing_instance: str | None,
    owner: str,
    expires_at_us: int,
    now_us: int,
    limits: RepositoryLimits,
    reservation_owner: str,
    reservation_identity_key: str,
    reservation_fence: int,
) -> GenerationRow:
    if reservation_identity_key != identity.key:
        raise RepositoryFenceError(
            "The preparation reservation belongs to another export identity."
        )
    leases.require_reservation(
        connection,
        owner=reservation_owner,
        identity_key=reservation_identity_key,
        fence=reservation_fence,
        producer_sha256=identity.producer_sha256,
        output_plan_sha256=identity.output_plan_sha256,
        spec_sha256=identity.spec_sha256,
        now_us=now_us,
    )
    artifacts.touch_producer(connection, identity.producer_sha256, now_us)
    current_revision = observation_revision(connection, identity.producer_sha256)
    if not 0 <= captured_observation_revision <= current_revision:
        raise ValueError("captured observation revision exceeds producer revision")
    artifacts.upsert_identity(connection, identity, now_us)
    artifacts.require_replacement(
        connection,
        table="identities",
        key_column="identity_key",
        key=identity.key,
        replacing_instance=replacing_instance,
        candidate_instance=instance,
        label="export generation",
    )
    existing = connection.execute(
        """
        SELECT metadata_json, content_bytes FROM generations
        WHERE identity_key = ? AND instance = ?
        """,
        (identity.key, instance),
    ).fetchone()
    if existing is not None and (
        bytes(existing[0]) != metadata or integer(existing[1]) != content_bytes
    ):
        raise ValueError("one export generation instance has conflicting metadata")
    replacement = (
        connection.execute(
            """
            SELECT content_bytes, metadata_bytes FROM generations
            WHERE identity_key = ? AND instance = ?
            """,
            (identity.key, replacing_instance),
        ).fetchone()
        if replacing_instance is not None and replacing_instance != instance
        else None
    )
    artifacts.admit_artifact(
        connection,
        kind="generation",
        content_bytes=content_bytes,
        metadata_bytes=len(metadata),
        exists=existing is not None,
        credit_content_bytes=integer(replacement[0]) if replacement else 0,
        credit_metadata_bytes=integer(replacement[1]) if replacement else 0,
        limits=limits,
    )
    connection.execute(
        """
        INSERT INTO generations(
            identity_key, instance, metadata_json, metadata_bytes,
            captured_observation_revision, content_bytes,
            created_at_us, accessed_at_us
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(identity_key, instance) DO UPDATE SET
            accessed_at_us = excluded.accessed_at_us,
            captured_observation_revision = MAX(
                generations.captured_observation_revision,
                excluded.captured_observation_revision
            )
        """,
        (
            identity.key,
            instance,
            metadata,
            len(metadata),
            captured_observation_revision,
            content_bytes,
            now_us,
            now_us,
        ),
    )
    connection.execute(
        "DELETE FROM generation_states WHERE identity_key = ? AND generation_instance = ?",
        (identity.key, instance),
    )
    connection.executemany(
        """
        INSERT INTO generation_states(
            identity_key, generation_instance, state_key, state_instance
        ) VALUES (?, ?, ?, ?)
        """,
        ((identity.key, instance, state.state_key, state.instance) for state in states),
    )
    connection.execute(
        """
        UPDATE identities SET current_instance = ?, touched_at_us = ?
        WHERE identity_key = ?
        """,
        (instance, now_us, identity.key),
    )
    leases.acquire_artifacts(
        connection,
        owner,
        (("generation", identity.key, instance),),
        expires_at_us,
    )
    return GenerationRow(
        identity,
        identity.key,
        instance,
        metadata,
        len(metadata),
        captured_observation_revision,
        content_bytes,
    )


def current(
    connection: sqlite3.Connection,
    identity: RepositoryIdentity,
    *,
    owner: str,
    expires_at_us: int,
    now_us: int,
) -> GenerationRow | None:
    raw_rows = rows(
        connection,
        "WHERE i.identity_key = ? AND g.instance = i.current_instance",
        (identity.key,),
    )
    if not raw_rows:
        return None
    row = generation_row(raw_rows[0])
    _touch(connection, row.identity_key, row.instance, now_us)
    leases.acquire_artifacts(
        connection,
        owner,
        (("generation", row.identity_key, row.instance),),
        expires_at_us,
    )
    return row


def by_instance(
    connection: sqlite3.Connection,
    identity: RepositoryIdentity,
    instance: str,
    *,
    owner: str,
    expires_at_us: int,
    now_us: int,
) -> GenerationRow | None:
    raw_rows = rows(
        connection,
        "WHERE i.identity_key = ? AND g.instance = ?",
        (identity.key, instance),
    )
    if not raw_rows:
        return None
    row = generation_row(raw_rows[0])
    _touch(connection, row.identity_key, row.instance, now_us)
    leases.acquire_artifacts(
        connection,
        owner,
        (("generation", row.identity_key, row.instance),),
        expires_at_us,
    )
    return row


def all_rows(connection: sqlite3.Connection) -> tuple[GenerationRow, ...]:
    return tuple(generation_row(row) for row in rows(connection))


def remove(connection: sqlite3.Connection, generation: GenerationRow) -> None:
    connection.execute(
        """
        UPDATE identities SET current_instance = NULL
        WHERE identity_key = ? AND current_instance = ?
        """,
        (generation.identity_key, generation.instance),
    )
    connection.execute(
        "DELETE FROM generations WHERE identity_key = ? AND instance = ?",
        (generation.identity_key, generation.instance),
    )
    connection.execute(
        "DELETE FROM identities WHERE identity_key = ? AND current_instance IS NULL",
        (generation.identity_key,),
    )


def memberships(connection: sqlite3.Connection) -> frozenset[tuple[str, str]]:
    return frozenset(
        (str(row[0]), str(row[1]))
        for row in connection.execute("SELECT state_key, state_instance FROM generation_states")
    )


def rows(
    connection: sqlite3.Connection,
    clause: str = "",
    parameters: tuple[object, ...] = (),
) -> list[tuple[object, ...]]:
    return connection.execute(
        f"""
        SELECT i.identity_key, i.producer_sha256, i.output_plan_sha256,
               i.spec_sha256, g.instance, g.metadata_json, g.metadata_bytes,
               g.captured_observation_revision, g.content_bytes
        FROM identities AS i
        JOIN generations AS g ON g.identity_key = i.identity_key
        {clause}
        ORDER BY g.accessed_at_us DESC, g.created_at_us DESC, g.instance DESC
        """,
        parameters,
    ).fetchall()


def _touch(
    connection: sqlite3.Connection,
    identity_key: str,
    instance: str,
    now_us: int,
) -> None:
    connection.execute(
        """
        UPDATE generations SET accessed_at_us = ?
        WHERE identity_key = ? AND instance = ?
        """,
        (now_us, identity_key, instance),
    )
    connection.execute(
        "UPDATE identities SET touched_at_us = ? WHERE identity_key = ?",
        (now_us, identity_key),
    )


__all__ = [
    "all_rows",
    "by_instance",
    "check_commit",
    "commit",
    "current",
    "memberships",
    "remove",
    "rows",
]
