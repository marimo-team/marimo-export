from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from marimo_export._repository.capabilities import (
    ArtifactKey,
    ArtifactRelease,
    LostLifecycle,
)
from marimo_export._repository.models import RepositoryFenceError
from marimo_export._repository.sqlite.records import integer


def acquire_artifacts(
    connection: sqlite3.Connection,
    owner: str,
    artifacts: Sequence[ArtifactKey],
    expires_at_us: int,
) -> None:
    connection.executemany(
        """
        INSERT INTO artifact_leases(
            owner, kind, artifact_key, instance, expires_at_us
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(owner, kind, artifact_key, instance) DO UPDATE SET
            expires_at_us = MAX(artifact_leases.expires_at_us, excluded.expires_at_us)
        """,
        ((owner, *artifact, expires_at_us) for artifact in artifacts),
    )


def renew_artifacts(
    connection: sqlite3.Connection,
    owner: str,
    artifacts: Sequence[ArtifactKey],
    expires_at_us: int,
    now_us: int,
) -> frozenset[ArtifactKey]:
    lost: set[ArtifactKey] = set()
    for artifact in artifacts:
        cursor = connection.execute(
            """
            UPDATE artifact_leases SET expires_at_us = MAX(expires_at_us, ?)
            WHERE owner = ? AND kind = ? AND artifact_key = ? AND instance = ?
              AND expires_at_us > ?
            """,
            (expires_at_us, owner, *artifact, now_us),
        )
        if cursor.rowcount != 1:
            lost.add(artifact)
    return frozenset(lost)


def release_artifacts(
    connection: sqlite3.Connection,
    owner: str,
    artifacts: Sequence[ArtifactRelease] | None = None,
) -> None:
    if artifacts is None:
        connection.execute("DELETE FROM artifact_leases WHERE owner = ?", (owner,))
        return
    connection.executemany(
        """
        DELETE FROM artifact_leases
        WHERE owner = ? AND kind = ? AND artifact_key = ? AND instance = ?
          AND expires_at_us = ?
        """,
        ((owner, *artifact, expires_at_us) for artifact, expires_at_us in artifacts),
    )


def acquire_staging(
    connection: sqlite3.Connection,
    owner: str,
    relative_path: str,
    expires_at_us: int,
) -> None:
    connection.execute(
        """
        INSERT INTO staging_leases(owner, relative_path, expires_at_us)
        VALUES (?, ?, ?)
        ON CONFLICT(owner, relative_path) DO UPDATE SET
            expires_at_us = excluded.expires_at_us
        """,
        (owner, relative_path, expires_at_us),
    )


def renew_staging(
    connection: sqlite3.Connection,
    owner: str,
    relative_paths: Sequence[str],
    expires_at_us: int,
    now_us: int,
) -> frozenset[str]:
    lost: set[str] = set()
    for path in relative_paths:
        cursor = connection.execute(
            """
            UPDATE staging_leases SET expires_at_us = MAX(expires_at_us, ?)
            WHERE owner = ? AND relative_path = ? AND expires_at_us > ?
            """,
            (expires_at_us, owner, path, now_us),
        )
        if cursor.rowcount != 1:
            lost.add(path)
    return frozenset(lost)


def release_staging(
    connection: sqlite3.Connection,
    owner: str,
    relative_paths: Sequence[str] | None = None,
) -> None:
    if relative_paths is None:
        connection.execute("DELETE FROM staging_leases WHERE owner = ?", (owner,))
        return
    connection.executemany(
        "DELETE FROM staging_leases WHERE owner = ? AND relative_path = ?",
        ((owner, path) for path in relative_paths),
    )


def active_staging(connection: sqlite3.Connection, now_us: int) -> frozenset[str]:
    connection.execute("DELETE FROM staging_leases WHERE expires_at_us <= ?", (now_us,))
    return frozenset(
        str(row[0])
        for row in connection.execute(
            "SELECT relative_path FROM staging_leases WHERE expires_at_us > ?",
            (now_us,),
        )
    )


def active_artifacts(
    connection: sqlite3.Connection,
    now_us: int,
) -> frozenset[ArtifactKey]:
    delete_expired(connection, now_us)
    return frozenset(
        (str(row[0]), str(row[1]), str(row[2]))
        for row in connection.execute(
            """
            SELECT kind, artifact_key, instance FROM artifact_leases
            WHERE expires_at_us > ?
            """,
            (now_us,),
        )
    )


def claim_reservation(
    connection: sqlite3.Connection,
    owner: str,
    identity_key: str,
    producer_sha256: str,
    output_plan_sha256: str,
    spec_sha256: str,
    expires_at_us: int,
    now_us: int,
) -> int | None:
    connection.execute(
        "DELETE FROM preparation_reservations WHERE expires_at_us <= ?",
        (now_us,),
    )
    row = connection.execute(
        """
        SELECT owner, fence, producer_sha256, output_plan_sha256, spec_sha256
        FROM preparation_reservations WHERE identity_key = ?
        """,
        (identity_key,),
    ).fetchone()
    if row is not None and row[0] != owner:
        return None
    if row is not None:
        if (str(row[2]), str(row[3]), str(row[4])) != (
            producer_sha256,
            output_plan_sha256,
            spec_sha256,
        ):
            raise RepositoryFenceError("The preparation reservation identity is stale.")
        fence = int(row[1])
        connection.execute(
            """
            UPDATE preparation_reservations SET expires_at_us = MAX(expires_at_us, ?)
            WHERE identity_key = ? AND owner = ? AND fence = ?
            """,
            (expires_at_us, identity_key, owner, fence),
        )
        return fence
    counter = connection.execute(
        "SELECT value FROM repository_counters WHERE name = 'preparation_fence'"
    ).fetchone()
    if counter is None:
        raise RuntimeError("The preparation fence counter is missing")
    fence = int(counter[0]) + 1
    connection.execute(
        "UPDATE repository_counters SET value = ? WHERE name = 'preparation_fence'",
        (fence,),
    )
    connection.execute(
        """
        INSERT INTO preparation_reservations(
            identity_key, owner, fence, producer_sha256,
            output_plan_sha256, spec_sha256, expires_at_us
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            identity_key,
            owner,
            fence,
            producer_sha256,
            output_plan_sha256,
            spec_sha256,
            expires_at_us,
        ),
    )
    return fence


def require_reservation(
    connection: sqlite3.Connection,
    *,
    owner: str,
    identity_key: str,
    fence: int,
    producer_sha256: str,
    output_plan_sha256: str,
    spec_sha256: str,
    now_us: int,
) -> None:
    row = connection.execute(
        """
        SELECT owner, fence, producer_sha256, output_plan_sha256,
               spec_sha256, expires_at_us
        FROM preparation_reservations
        WHERE identity_key = ?
        """,
        (identity_key,),
    ).fetchone()
    if (
        row is None
        or str(row[0]) != owner
        or integer(row[1]) != fence
        or str(row[2]) != producer_sha256
        or str(row[3]) != output_plan_sha256
        or str(row[4]) != spec_sha256
        or integer(row[5]) <= now_us
    ):
        raise RepositoryFenceError("The preparation reservation is stale.")


def renew_reservations(
    connection: sqlite3.Connection,
    owner: str,
    identities: Sequence[str],
    expires_at_us: int,
    now_us: int,
) -> frozenset[str]:
    lost: set[str] = set()
    for identity in identities:
        cursor = connection.execute(
            """
            UPDATE preparation_reservations SET expires_at_us = MAX(expires_at_us, ?)
            WHERE identity_key = ? AND owner = ? AND expires_at_us > ?
            """,
            (expires_at_us, identity, owner, now_us),
        )
        if cursor.rowcount != 1:
            lost.add(identity)
    return frozenset(lost)


def release_reservations(
    connection: sqlite3.Connection,
    owner: str,
    identities: Sequence[str] | None = None,
) -> None:
    if identities is None:
        connection.execute(
            "DELETE FROM preparation_reservations WHERE owner = ?",
            (owner,),
        )
        return
    connection.executemany(
        "DELETE FROM preparation_reservations WHERE owner = ? AND identity_key = ?",
        ((owner, identity) for identity in identities),
    )


def delete_expired(connection: sqlite3.Connection, now_us: int) -> None:
    for table in ("artifact_leases", "staging_leases", "preparation_reservations"):
        connection.execute(f"DELETE FROM {table} WHERE expires_at_us <= ?", (now_us,))


def renew_lifecycle(
    connection: sqlite3.Connection,
    *,
    owner: str,
    artifacts: Sequence[ArtifactKey],
    staging: Sequence[str],
    reservations: Sequence[str],
    expires_at_us: int,
    now_us: int,
) -> LostLifecycle:
    return LostLifecycle(
        artifacts=renew_artifacts(connection, owner, artifacts, expires_at_us, now_us),
        staging=renew_staging(connection, owner, staging, expires_at_us, now_us),
        reservations=renew_reservations(
            connection,
            owner,
            reservations,
            expires_at_us,
            now_us,
        ),
    )


def release_lifecycle(
    connection: sqlite3.Connection,
    *,
    owner: str,
    artifacts: Sequence[ArtifactRelease] | None = None,
    staging: Sequence[str] | None = None,
    reservations: Sequence[str] | None = None,
) -> None:
    release_artifacts(connection, owner, artifacts)
    release_staging(connection, owner, staging)
    release_reservations(connection, owner, reservations)


__all__ = [
    "ArtifactKey",
    "ArtifactRelease",
    "acquire_artifacts",
    "acquire_staging",
    "active_artifacts",
    "active_staging",
    "claim_reservation",
    "delete_expired",
    "release_artifacts",
    "release_lifecycle",
    "release_reservations",
    "release_staging",
    "renew_artifacts",
    "renew_lifecycle",
    "renew_reservations",
    "renew_staging",
    "require_reservation",
]
