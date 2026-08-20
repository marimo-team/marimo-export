from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence

from marimo_export._json import canonical_bytes, sha256_bytes
from marimo_export._repository.models import (
    MAX_SQLITE_INTEGER,
    RepositoryIdentity,
    digest,
)
from marimo_export._repository.sqlite import generations, leases
from marimo_export._repository.sqlite.records import (
    GenerationRow,
    RecoverySnapshot,
    StateRow,
    generation_row,
    integer,
    state_row,
)


def snapshot(connection: sqlite3.Connection, now_us: int) -> RecoverySnapshot:
    _remove_malformed_rows(connection)
    _repair_stale_pointers(connection)
    corrupt_producers = frozenset(
        str(row[0])
        for row in connection.execute("SELECT producer_sha256, observation_revision FROM producers")
        if not _valid_revision(row[1])
    )
    return RecoverySnapshot(
        corrupt_producers,
        leases.active_staging(connection, now_us),
        _state_rows(connection),
        _generation_rows(connection),
    )


def recover_artifacts(
    connection: sqlite3.Connection,
    *,
    snapshot: RecoverySnapshot,
    now_us: int,
    invalid_states: Sequence[StateRow],
    invalid_generations: Sequence[GenerationRow],
    retired_states: Mapping[tuple[str, str], tuple[str, int]],
    retired_generations: Mapping[tuple[str, str], tuple[str, int]],
) -> tuple[tuple[StateRow, ...], tuple[GenerationRow, ...]]:
    corrupt_producers = {
        str(row[0])
        for row in connection.execute("SELECT producer_sha256, observation_revision FROM producers")
        if str(row[0]) in snapshot.corrupt_producers and not _valid_revision(row[1])
    }
    current_states = {(row.state_key, row.instance): row for row in _state_rows(connection)}
    matched_states = tuple(
        row for row in invalid_states if current_states.get((row.state_key, row.instance)) == row
    )
    current_generations = {
        (row.identity_key, row.instance): row for row in _generation_rows(connection)
    }
    invalid_generation_keys = {
        (row.identity_key, row.instance)
        for row in invalid_generations
        if current_generations.get((row.identity_key, row.instance)) == row
    }
    invalid_state_keys = {(row.state_key, row.instance) for row in matched_states}
    invalid_generation_keys.update(
        (str(raw[0]), str(raw[1]))
        for state_key, state_instance in invalid_state_keys
        for raw in connection.execute(
            """
            SELECT identity_key, generation_instance
            FROM generation_states
            WHERE state_key = ? AND state_instance = ?
            """,
            (state_key, state_instance),
        )
    )
    matched_generations = tuple(
        row for key, row in current_generations.items() if key in invalid_generation_keys
    )
    retired = (
        *(
            retired_states[(row.state_key, row.instance)]
            for row in matched_states
            if (row.state_key, row.instance) in retired_states
        ),
        *(
            retired_generations[(row.identity_key, row.instance)]
            for row in matched_generations
            if (row.identity_key, row.instance) in retired_generations
        ),
    )
    connection.executemany(
        """
        INSERT INTO retired_artifacts(relative_path, content_bytes, created_at_us)
        VALUES (?, ?, ?)
        ON CONFLICT(relative_path) DO UPDATE SET
            content_bytes = excluded.content_bytes
        """,
        ((path, content_bytes, now_us) for path, content_bytes in retired),
    )
    connection.executemany(
        """
        UPDATE identities SET current_instance = NULL
        WHERE identity_key = ? AND current_instance = ?
        """,
        ((row.identity_key, row.instance) for row in matched_generations),
    )
    connection.executemany(
        "DELETE FROM generations WHERE identity_key = ? AND instance = ?",
        ((row.identity_key, row.instance) for row in matched_generations),
    )
    connection.executemany(
        """
        UPDATE state_scopes SET current_instance = NULL
        WHERE state_key = ? AND current_instance = ?
        """,
        ((row.state_key, row.instance) for row in matched_states),
    )
    connection.executemany(
        "DELETE FROM prepared_states WHERE state_key = ? AND instance = ?",
        ((row.state_key, row.instance) for row in matched_states),
    )
    connection.executemany(
        "DELETE FROM producers WHERE producer_sha256 = ?",
        ((producer,) for producer in corrupt_producers),
    )
    return matched_states, matched_generations


def _repair_stale_pointers(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        UPDATE identities SET current_instance = NULL
        WHERE current_instance IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM generations
            WHERE generations.identity_key = identities.identity_key
              AND generations.instance = identities.current_instance
          )
        """
    )
    connection.execute(
        """
        UPDATE state_scopes SET current_instance = NULL
        WHERE current_instance IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM prepared_states
            WHERE prepared_states.state_key = state_scopes.state_key
              AND prepared_states.instance = state_scopes.current_instance
          )
        """
    )


def _remove_malformed_rows(connection: sqlite3.Connection) -> None:
    producer_rows = connection.execute(
        "SELECT producer_sha256, touched_at_us FROM producers"
    ).fetchall()
    bad_producers = [
        raw[0]
        for raw in producer_rows
        if not _is_digest(raw[0]) or not _is_nonnegative_integer(raw[1])
    ]
    valid_producers = [raw[0] for raw in producer_rows if raw[0] not in bad_producers]
    bad_state_keys = [
        raw[0]
        for raw in connection.execute(
            """
            SELECT state_key, producer_sha256, output_plan_sha256,
                   state_fingerprint, current_instance, touched_at_us
            FROM state_scopes
            """
        )
        if not _valid_state_scope(raw, valid_producers)
    ]
    bad_identity_keys = [
        raw[0]
        for raw in connection.execute(
            """
            SELECT identity_key, producer_sha256, output_plan_sha256,
                   spec_sha256, current_instance, touched_at_us
            FROM identities
            """
        )
        if not _valid_identity(raw, valid_producers)
    ]
    bad_state_instances = [
        (raw[0], raw[1])
        for raw in connection.execute(
            """
            SELECT state_key, instance, metadata_json, metadata_bytes,
                   content_bytes, created_at_us, accessed_at_us
            FROM prepared_states
            """
        )
        if not _valid_artifact_row(raw)
    ]
    bad_state_instances.extend(
        connection.execute(
            """
            SELECT p.state_key, p.instance
            FROM prepared_states AS p
            WHERE NOT EXISTS (
                SELECT 1 FROM state_scopes AS s
                WHERE s.state_key = p.state_key
            )
            """
        )
    )
    bad_generation_instances = [
        (raw[0], raw[1])
        for raw in connection.execute(
            """
            SELECT identity_key, instance, metadata_json, metadata_bytes,
                   content_bytes, created_at_us, accessed_at_us,
                   captured_observation_revision
            FROM generations
            """
        )
        if not _valid_artifact_row(raw[:7]) or not _is_nonnegative_integer(raw[7])
    ]
    bad_generation_instances.extend(
        connection.execute(
            """
            SELECT g.identity_key, g.instance
            FROM generations AS g
            WHERE NOT EXISTS (
                SELECT 1 FROM identities AS i
                WHERE i.identity_key = g.identity_key
            )
            """
        )
    )
    for raw in connection.execute(
        """
        SELECT identity_key, generation_instance, state_key, state_instance
        FROM generation_states
        """
    ):
        generation_exists = connection.execute(
            """
            SELECT 1 FROM generations
            WHERE identity_key = ? AND instance = ?
            """,
            (raw[0], raw[1]),
        ).fetchone()
        state_exists = connection.execute(
            """
            SELECT 1 FROM prepared_states
            WHERE state_key = ? AND instance = ?
            """,
            (raw[2], raw[3]),
        ).fetchone()
        if (
            not all(_is_digest(value) for value in raw)
            or generation_exists is None
            or state_exists is None
        ):
            bad_generation_instances.append((raw[0], raw[1]))

    for state_key in bad_state_keys:
        bad_state_instances.extend(
            connection.execute(
                "SELECT state_key, instance FROM prepared_states WHERE state_key = ?",
                (state_key,),
            )
        )
    for identity_key in bad_identity_keys:
        bad_generation_instances.extend(
            connection.execute(
                "SELECT identity_key, instance FROM generations WHERE identity_key = ?",
                (identity_key,),
            )
        )
    for state_key, instance in bad_state_instances:
        bad_generation_instances.extend(
            connection.execute(
                """
                SELECT identity_key, generation_instance
                FROM generation_states
                WHERE state_key = ? AND state_instance = ?
                """,
                (state_key, instance),
            )
        )

    for identity_key, instance in bad_generation_instances:
        connection.execute(
            """
            DELETE FROM generation_states
            WHERE identity_key = ? AND generation_instance = ?
            """,
            (identity_key, instance),
        )
        connection.execute(
            "DELETE FROM generations WHERE identity_key = ? AND instance = ?",
            (identity_key, instance),
        )
    for identity_key in bad_identity_keys:
        connection.execute(
            "DELETE FROM generation_states WHERE identity_key = ?",
            (identity_key,),
        )
        connection.execute(
            "DELETE FROM generations WHERE identity_key = ?",
            (identity_key,),
        )
        connection.execute(
            "DELETE FROM identities WHERE identity_key = ?",
            (identity_key,),
        )
    for state_key, instance in bad_state_instances:
        connection.execute(
            "DELETE FROM generation_states WHERE state_key = ? AND state_instance = ?",
            (state_key, instance),
        )
        connection.execute(
            "DELETE FROM prepared_states WHERE state_key = ? AND instance = ?",
            (state_key, instance),
        )
    for state_key in bad_state_keys:
        connection.execute(
            "DELETE FROM generation_states WHERE state_key = ?",
            (state_key,),
        )
        connection.execute("DELETE FROM prepared_states WHERE state_key = ?", (state_key,))
        connection.execute("DELETE FROM state_scopes WHERE state_key = ?", (state_key,))
    for producer in bad_producers:
        connection.execute("DELETE FROM producers WHERE producer_sha256 = ?", (producer,))

    connection.execute(
        """
        DELETE FROM observations
        WHERE NOT EXISTS (
            SELECT 1 FROM producers
            WHERE producers.producer_sha256 = observations.producer_sha256
        )
        """
    )
    connection.execute(
        """
        DELETE FROM observation_events
        WHERE NOT EXISTS (
            SELECT 1 FROM producers
            WHERE producers.producer_sha256 = observation_events.producer_sha256
        )
        """
    )
    connection.execute(
        """
        DELETE FROM generation_states
        WHERE NOT EXISTS (
            SELECT 1 FROM generations
            WHERE generations.identity_key = generation_states.identity_key
              AND generations.instance = generation_states.generation_instance
        ) OR NOT EXISTS (
            SELECT 1 FROM prepared_states
            WHERE prepared_states.state_key = generation_states.state_key
              AND prepared_states.instance = generation_states.state_instance
        )
        """
    )
    connection.execute(
        """
        DELETE FROM generations
        WHERE NOT EXISTS (
            SELECT 1 FROM identities
            WHERE identities.identity_key = generations.identity_key
        )
        """
    )
    connection.execute(
        """
        DELETE FROM prepared_states
        WHERE NOT EXISTS (
            SELECT 1 FROM state_scopes
            WHERE state_scopes.state_key = prepared_states.state_key
        )
        """
    )


def _valid_state_scope(raw: tuple[object, ...], valid_producers: list[object]) -> bool:
    state_key, producer, output_plan, fingerprint, current, touched = raw
    if (
        producer not in valid_producers
        or not all(_is_digest(value) for value in raw[:4])
        or (current is not None and not _is_digest(current))
        or not _is_nonnegative_integer(touched)
    ):
        return False
    expected = sha256_bytes(
        canonical_bytes(
            {
                "output_plan_sha256": output_plan,
                "producer_sha256": producer,
                "state_fingerprint": fingerprint,
            }
        )
    )
    return state_key == expected


def _valid_identity(raw: tuple[object, ...], valid_producers: list[object]) -> bool:
    identity_key, producer, output_plan, spec, current, touched = raw
    if (
        producer not in valid_producers
        or not all(_is_digest(value) for value in raw[:4])
        or (current is not None and not _is_digest(current))
        or not _is_nonnegative_integer(touched)
    ):
        return False
    return (
        identity_key
        == RepositoryIdentity(
            digest(producer, "producer_sha256"),
            digest(output_plan, "output_plan_sha256"),
            digest(spec, "spec_sha256"),
        ).key
    )


def _valid_artifact_row(raw: tuple[object, ...]) -> bool:
    key, instance, metadata, metadata_bytes, content_bytes, created, accessed = raw
    return (
        _is_digest(key)
        and _is_digest(instance)
        and isinstance(metadata, bytes)
        and all(
            _is_nonnegative_integer(value)
            for value in (metadata_bytes, content_bytes, created, accessed)
        )
    )


def _is_digest(value: object) -> bool:
    try:
        digest(value, "repository digest")
    except ValueError:
        return False
    return True


def _is_nonnegative_integer(value: object) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_SQLITE_INTEGER
    )


def _state_rows(connection: sqlite3.Connection) -> tuple[StateRow, ...]:
    rows: list[StateRow] = []
    raw_rows = connection.execute(
        """
        SELECT s.state_key, s.producer_sha256, s.output_plan_sha256,
               s.state_fingerprint, p.instance, p.metadata_json,
               p.metadata_bytes, p.content_bytes
        FROM state_scopes AS s
        JOIN prepared_states AS p ON p.state_key = s.state_key
        """
    )
    for raw in raw_rows:
        try:
            rows.append(state_row(raw))
            continue
        except (TypeError, ValueError):
            pass
        try:
            state_key = digest(raw[0], "state key")
            producer = digest(raw[1], "producer_sha256")
            output_plan = digest(raw[2], "output_plan_sha256")
            fingerprint = digest(raw[3], "state_fingerprint")
            instance = digest(raw[4], "prepared state instance")
            expected_key = sha256_bytes(
                canonical_bytes(
                    {
                        "output_plan_sha256": output_plan,
                        "producer_sha256": producer,
                        "state_fingerprint": fingerprint,
                    }
                )
            )
            if state_key != expected_key:
                raise ValueError("state key")
            content_bytes = integer(raw[7])
            if content_bytes < 0:
                raise ValueError("content bytes")
        except (TypeError, ValueError):
            continue
        rows.append(
            StateRow(
                state_key,
                producer,
                output_plan,
                fingerprint,
                instance,
                b"",
                0,
                content_bytes,
            )
        )
    return tuple(rows)


def _generation_rows(connection: sqlite3.Connection) -> tuple[GenerationRow, ...]:
    rows: list[GenerationRow] = []
    for raw in generations.rows(connection):
        try:
            rows.append(generation_row(raw))
            continue
        except (TypeError, ValueError):
            pass
        try:
            identity = RepositoryIdentity(
                producer_sha256=digest(raw[1], "producer_sha256"),
                output_plan_sha256=digest(raw[2], "output_plan_sha256"),
                spec_sha256=digest(raw[3], "spec_sha256"),
            )
            if identity.key != digest(raw[0], "identity key"):
                raise ValueError("identity key")
            instance = digest(raw[4], "generation instance")
            captured_revision = integer(raw[7])
            content_bytes = integer(raw[8])
            if captured_revision < 0 or content_bytes < 0:
                raise ValueError("generation integers")
        except (TypeError, ValueError):
            continue
        rows.append(
            GenerationRow(
                identity,
                identity.key,
                instance,
                b"",
                0,
                captured_revision,
                content_bytes,
            )
        )
    return tuple(rows)


def _valid_revision(value: object) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_SQLITE_INTEGER
    )


__all__ = ["recover_artifacts", "snapshot"]
