from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from marimo_export._repository.models import RepositoryLimits
from marimo_export._repository.sqlite import artifacts, leases
from marimo_export._repository.sqlite.records import StateRow, integer, state_row


def check_commit(
    connection: sqlite3.Connection,
    *,
    state_key: str,
    producer_sha256: str,
    output_plan_sha256: str,
    instance: str,
    replacing_instance: str | None,
    reservation_owner: str,
    reservation_identity_key: str,
    reservation_fence: int,
    reservation_spec_sha256: str,
    now_us: int,
) -> None:
    leases.require_reservation(
        connection,
        owner=reservation_owner,
        identity_key=reservation_identity_key,
        fence=reservation_fence,
        producer_sha256=producer_sha256,
        output_plan_sha256=output_plan_sha256,
        spec_sha256=reservation_spec_sha256,
        now_us=now_us,
    )
    artifacts.require_replacement(
        connection,
        table="state_scopes",
        key_column="state_key",
        key=state_key,
        replacing_instance=replacing_instance,
        candidate_instance=instance,
        label="prepared state",
    )


def commit(
    connection: sqlite3.Connection,
    *,
    state_key: str,
    producer_sha256: str,
    output_plan_sha256: str,
    state_fingerprint: str,
    instance: str,
    metadata: bytes,
    content_bytes: int,
    replacing_instance: str | None,
    owner: str,
    expires_at_us: int,
    now_us: int,
    limits: RepositoryLimits,
    reservation_owner: str,
    reservation_identity_key: str,
    reservation_fence: int,
    reservation_spec_sha256: str,
) -> StateRow:
    leases.require_reservation(
        connection,
        owner=reservation_owner,
        identity_key=reservation_identity_key,
        fence=reservation_fence,
        producer_sha256=producer_sha256,
        output_plan_sha256=output_plan_sha256,
        spec_sha256=reservation_spec_sha256,
        now_us=now_us,
    )
    artifacts.touch_producer(connection, producer_sha256, now_us)
    connection.execute(
        """
        INSERT INTO state_scopes(
            state_key, producer_sha256, output_plan_sha256,
            state_fingerprint, touched_at_us
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(state_key) DO UPDATE SET touched_at_us = excluded.touched_at_us
        """,
        (
            state_key,
            producer_sha256,
            output_plan_sha256,
            state_fingerprint,
            now_us,
        ),
    )
    artifacts.require_replacement(
        connection,
        table="state_scopes",
        key_column="state_key",
        key=state_key,
        replacing_instance=replacing_instance,
        candidate_instance=instance,
        label="prepared state",
    )
    existing = connection.execute(
        """
        SELECT metadata_json, content_bytes FROM prepared_states
        WHERE state_key = ? AND instance = ?
        """,
        (state_key, instance),
    ).fetchone()
    if existing is not None and (
        bytes(existing[0]) != metadata or integer(existing[1]) != content_bytes
    ):
        raise ValueError("one prepared state instance has conflicting metadata")
    replacement = (
        connection.execute(
            """
            SELECT content_bytes, metadata_bytes FROM prepared_states
            WHERE state_key = ? AND instance = ?
            """,
            (state_key, replacing_instance),
        ).fetchone()
        if replacing_instance is not None and replacing_instance != instance
        else None
    )
    artifacts.admit_artifact(
        connection,
        kind="state",
        content_bytes=content_bytes,
        metadata_bytes=len(metadata),
        exists=existing is not None,
        credit_content_bytes=integer(replacement[0]) if replacement else 0,
        credit_metadata_bytes=integer(replacement[1]) if replacement else 0,
        limits=limits,
    )
    connection.execute(
        """
        INSERT INTO prepared_states(
            state_key, instance, metadata_json, metadata_bytes,
            content_bytes, created_at_us, accessed_at_us
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(state_key, instance) DO UPDATE SET
            accessed_at_us = excluded.accessed_at_us
        """,
        (
            state_key,
            instance,
            metadata,
            len(metadata),
            content_bytes,
            now_us,
            now_us,
        ),
    )
    connection.execute(
        """
        UPDATE state_scopes SET current_instance = ?, touched_at_us = ?
        WHERE state_key = ?
        """,
        (instance, now_us, state_key),
    )
    leases.acquire_artifacts(
        connection,
        owner,
        (("state", state_key, instance),),
        expires_at_us,
    )
    return StateRow(
        state_key,
        producer_sha256,
        output_plan_sha256,
        state_fingerprint,
        instance,
        metadata,
        len(metadata),
        content_bytes,
    )


def current(
    connection: sqlite3.Connection,
    *,
    producer_sha256: str,
    output_plan_sha256: str,
    state_fingerprints: Sequence[str],
    owner: str,
    expires_at_us: int,
    now_us: int,
) -> tuple[StateRow, ...]:
    if not state_fingerprints:
        return ()
    placeholders = ",".join("?" for _ in state_fingerprints)
    raw = connection.execute(
        f"""
        SELECT s.state_key, s.producer_sha256, s.output_plan_sha256,
               s.state_fingerprint, p.instance, p.metadata_json,
               p.metadata_bytes, p.content_bytes
        FROM state_scopes AS s
        JOIN prepared_states AS p
          ON p.state_key = s.state_key AND p.instance = s.current_instance
        WHERE s.producer_sha256 = ? AND s.output_plan_sha256 = ?
          AND s.state_fingerprint IN ({placeholders})
        """,
        (producer_sha256, output_plan_sha256, *state_fingerprints),
    ).fetchall()
    rows = tuple(state_row(row) for row in raw)
    artifacts_to_renew = tuple(("state", row.state_key, row.instance) for row in rows)
    leases.acquire_artifacts(connection, owner, artifacts_to_renew, expires_at_us)
    connection.executemany(
        """
        UPDATE prepared_states SET accessed_at_us = ?
        WHERE state_key = ? AND instance = ?
        """,
        ((now_us, row.state_key, row.instance) for row in rows),
    )
    return rows


def all_rows(connection: sqlite3.Connection) -> tuple[StateRow, ...]:
    rows = connection.execute(
        """
        SELECT s.state_key, s.producer_sha256, s.output_plan_sha256,
               s.state_fingerprint, p.instance, p.metadata_json,
               p.metadata_bytes, p.content_bytes
        FROM state_scopes AS s
        JOIN prepared_states AS p ON p.state_key = s.state_key
        """
    ).fetchall()
    return tuple(state_row(row) for row in rows)


def remove(connection: sqlite3.Connection, state: StateRow) -> None:
    connection.execute(
        """
        UPDATE state_scopes SET current_instance = NULL
        WHERE state_key = ? AND current_instance = ?
        """,
        (state.state_key, state.instance),
    )
    connection.execute(
        "DELETE FROM prepared_states WHERE state_key = ? AND instance = ?",
        (state.state_key, state.instance),
    )
    connection.execute(
        "DELETE FROM state_scopes WHERE state_key = ? AND current_instance IS NULL",
        (state.state_key,),
    )


__all__ = ["all_rows", "check_commit", "commit", "current", "remove"]
