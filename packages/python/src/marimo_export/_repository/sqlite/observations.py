from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import cast

from marimo_export._json import canonical_bytes
from marimo_export._repository.models import (
    MAX_SQLITE_INTEGER,
    ObservationSnapshot,
    ObservedState,
    RepositoryError,
    RepositoryLimits,
    SnapshotObservation,
)
from marimo_export._repository.sqlite.records import integer
from marimo_export.wire import parse_canonical_json, state_fingerprint


def record_observation(
    connection: sqlite3.Connection,
    *,
    producer_sha256: str,
    fingerprint: str,
    input_names: bytes,
    values: bytes,
    occurrences: int,
    now_us: int,
    limits: RepositoryLimits,
) -> int:
    _touch_producer(connection, producer_sha256, now_us)
    current = observation_revision(connection, producer_sha256)
    if occurrences > MAX_SQLITE_INTEGER - current:
        raise ValueError("observation revision exceeds SQLite's integer range")
    revision = current + occurrences
    connection.execute(
        "UPDATE producers SET observation_revision = ? WHERE producer_sha256 = ?",
        (revision, producer_sha256),
    )
    connection.execute(
        """
        INSERT INTO observations(
            producer_sha256, fingerprint, input_names, values_json,
            byte_count, observed_order
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(producer_sha256, fingerprint) DO UPDATE SET
            input_names = excluded.input_names,
            values_json = excluded.values_json,
            byte_count = excluded.byte_count,
            observed_order = excluded.observed_order
        """,
        (producer_sha256, fingerprint, input_names, values, len(values), revision),
    )
    connection.execute(
        """
        INSERT INTO observation_events(
            producer_sha256, observed_order, fingerprint, input_names,
            values_json, byte_count
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (producer_sha256, revision, fingerprint, input_names, values, len(values)),
    )
    _prune_observation_table(connection, "observations", producer_sha256, limits)
    _prune_observation_table(connection, "observation_events", producer_sha256, limits)
    return revision


def advance_revision(
    connection: sqlite3.Connection,
    producer_sha256: str,
    occurrences: int,
    now_us: int,
) -> int:
    _touch_producer(connection, producer_sha256, now_us)
    current = observation_revision(connection, producer_sha256)
    if occurrences > MAX_SQLITE_INTEGER - current:
        raise ValueError("observation revision exceeds SQLite's integer range")
    revision = current + occurrences
    connection.execute(
        "UPDATE producers SET observation_revision = ? WHERE producer_sha256 = ?",
        (revision, producer_sha256),
    )
    return revision


def observation_revision(connection: sqlite3.Connection, producer_sha256: str) -> int:
    row = connection.execute(
        "SELECT observation_revision FROM producers WHERE producer_sha256 = ?",
        (producer_sha256,),
    ).fetchone()
    if row is None:
        return 0
    value = row[0]
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > MAX_SQLITE_INTEGER
    ):
        raise RepositoryError("The durable observation revision is corrupt.")
    return value


def observations(
    connection: sqlite3.Connection,
    producer_sha256: str,
    input_names: bytes,
) -> tuple[tuple[ObservedState, ...], bool]:
    maximum = observation_revision(connection, producer_sha256)
    rows = connection.execute(
        """
        SELECT rowid, fingerprint, input_names, values_json,
               byte_count, observed_order
        FROM observations
        WHERE producer_sha256 = ? AND input_names = ?
        ORDER BY fingerprint
        """,
        (producer_sha256, input_names),
    ).fetchall()
    parsed, corrupt = _validated_rows(
        connection,
        table="observations",
        producer_sha256=producer_sha256,
        rows=rows,
        maximum_revision=maximum,
    )
    return tuple(row.state for row in parsed), corrupt


def latest_observation(
    connection: sqlite3.Connection,
    producer_sha256: str,
    input_names: bytes,
    through_revision: int | None,
) -> tuple[ObservedState | None, bool]:
    maximum = observation_revision(connection, producer_sha256)
    if through_revision is not None and not 0 <= through_revision <= maximum:
        raise ValueError("through_revision must belong to the producer observation history")
    rows = connection.execute(
        """
        SELECT rowid, fingerprint, input_names, values_json,
               byte_count, observed_order
        FROM observation_events
        WHERE producer_sha256 = ? AND input_names = ?
        ORDER BY observed_order DESC
        """,
        (producer_sha256, input_names),
    ).fetchall()
    parsed, corrupt = _validated_rows(
        connection,
        table="observation_events",
        producer_sha256=producer_sha256,
        rows=rows,
        maximum_revision=maximum,
    )
    cutoff = maximum if through_revision is None else through_revision
    return next((row.state for row in parsed if row.state.revision <= cutoff), None), corrupt


def observation_snapshot(
    connection: sqlite3.Connection,
    producer_sha256: str,
) -> tuple[ObservationSnapshot, bool]:
    revision = observation_revision(connection, producer_sha256)
    observed_rows = connection.execute(
        """
        SELECT rowid, fingerprint, input_names, values_json,
               byte_count, observed_order
        FROM observations
        WHERE producer_sha256 = ?
        ORDER BY observed_order, fingerprint
        """,
        (producer_sha256,),
    ).fetchall()
    event_rows = connection.execute(
        """
        SELECT rowid, fingerprint, input_names, values_json,
               byte_count, observed_order
        FROM observation_events
        WHERE producer_sha256 = ?
        ORDER BY observed_order DESC
        """,
        (producer_sha256,),
    ).fetchall()
    observed, corrupt_observed = _validated_rows(
        connection,
        table="observations",
        producer_sha256=producer_sha256,
        rows=observed_rows,
        maximum_revision=revision,
    )
    events, corrupt_events = _validated_rows(
        connection,
        table="observation_events",
        producer_sha256=producer_sha256,
        rows=event_rows,
        maximum_revision=revision,
    )
    latest: dict[bytes, _ValidatedObservation] = {}
    for row in events:
        latest.setdefault(row.input_names, row)
    return (
        ObservationSnapshot(
            producer_sha256,
            revision,
            tuple(SnapshotObservation(row.input_names_tuple, row.state) for row in observed),
            tuple(SnapshotObservation(row.input_names_tuple, row.state) for row in latest.values()),
        ),
        corrupt_observed or corrupt_events,
    )


def clear_observations(connection: sqlite3.Connection, producer_sha256: str) -> int:
    row = connection.execute(
        "SELECT COUNT(*) FROM observations WHERE producer_sha256 = ?",
        (producer_sha256,),
    ).fetchone()
    count = integer(row[0]) if row is not None else 0
    connection.execute(
        "DELETE FROM observations WHERE producer_sha256 = ?",
        (producer_sha256,),
    )
    connection.execute(
        "DELETE FROM observation_events WHERE producer_sha256 = ?",
        (producer_sha256,),
    )
    return count


class _ValidatedObservation:
    __slots__ = ("input_names", "input_names_tuple", "state")

    def __init__(self, input_names: bytes, names: tuple[str, ...], state: ObservedState) -> None:
        self.input_names = input_names
        self.input_names_tuple = names
        self.state = state


def _validated_rows(
    connection: sqlite3.Connection,
    *,
    table: str,
    producer_sha256: str,
    rows: Sequence[tuple[object, ...]],
    maximum_revision: int,
) -> tuple[tuple[_ValidatedObservation, ...], bool]:
    if table not in {"observations", "observation_events"}:
        raise ValueError("observation validation requires a known table")
    valid: list[_ValidatedObservation] = []
    corrupt = False
    for row in rows:
        try:
            valid.append(_validate_row(producer_sha256, row, maximum_revision))
        except (TypeError, ValueError):
            corrupt = True
            connection.execute(f"DELETE FROM {table} WHERE rowid = ?", (row[0],))
            if table == "observations":
                connection.execute(
                    """
                    DELETE FROM observation_events
                    WHERE producer_sha256 = ?
                      AND (observed_order = ? OR fingerprint = ?)
                    """,
                    (producer_sha256, row[5], row[1]),
                )
    return tuple(valid), corrupt


def _validate_row(
    producer_sha256: str,
    row: tuple[object, ...],
    maximum_revision: int,
) -> _ValidatedObservation:
    fingerprint, input_names, values, byte_count, revision = row[1:]
    if not isinstance(fingerprint, str):
        raise TypeError("observation fingerprint")
    if not isinstance(input_names, bytes) or not isinstance(values, bytes):
        raise TypeError("observation canonical bytes")
    if integer(byte_count) != len(values):
        raise ValueError("observation byte count")
    parsed_revision = integer(revision)
    if not 1 <= parsed_revision <= maximum_revision:
        raise ValueError("observation revision")
    raw_names = parse_canonical_json(input_names, "observation input names")
    raw_values = parse_canonical_json(values, "observation values")
    if not isinstance(raw_names, list) or any(not isinstance(name, str) for name in raw_names):
        raise TypeError("observation input names")
    names = cast(list[str], raw_names)
    if names != sorted(set(names)):
        raise ValueError("observation input names")
    if not isinstance(raw_values, dict) or names != sorted(raw_values):
        raise ValueError("observation values")
    value_map = cast(dict[str, object], raw_values)
    if state_fingerprint(value_map) != fingerprint:
        raise ValueError("observation fingerprint")
    state = ObservedState(
        producer_sha256=producer_sha256,
        revision=parsed_revision,
        values=value_map,
    )
    return _ValidatedObservation(input_names, tuple(names), state)


def _touch_producer(
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


def _prune_observation_table(
    connection: sqlite3.Connection,
    table: str,
    producer_sha256: str,
    limits: RepositoryLimits,
) -> None:
    order = "observed_order DESC, fingerprint DESC"
    rows = connection.execute(
        f"SELECT rowid, byte_count FROM {table} WHERE producer_sha256 = ? ORDER BY {order}",
        (producer_sha256,),
    ).fetchall()
    kept_bytes = 0
    victims: list[int] = []
    for index, row in enumerate(rows):
        row_bytes = integer(row[1])
        if (
            index >= limits.observations_per_producer
            or kept_bytes + row_bytes > limits.observation_relation_bytes
        ):
            victims.append(integer(row[0]))
        else:
            kept_bytes += row_bytes
    connection.executemany(
        f"DELETE FROM {table} WHERE rowid = ?",
        ((rowid,) for rowid in victims),
    )


def input_names_bytes(names: tuple[str, ...]) -> bytes:
    if names != tuple(sorted(set(names))) or any(not isinstance(name, str) for name in names):
        raise ValueError("input names must be a sorted unique tuple of strings")
    return canonical_bytes(list(names))


__all__ = [
    "advance_revision",
    "clear_observations",
    "input_names_bytes",
    "latest_observation",
    "observation_revision",
    "observation_snapshot",
    "observations",
    "record_observation",
]
