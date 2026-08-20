from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

from marimo_export._repository.models import RepositoryLimits
from marimo_export._repository.sqlite import leases
from marimo_export._repository.sqlite.records import (
    GenerationRow,
    StateRow,
    generation_row,
    integer,
    state_row,
)


@dataclass(frozen=True, slots=True)
class RetentionVictims:
    states: tuple[StateRow, ...]
    generations: tuple[GenerationRow, ...]

    @property
    def content_bytes(self) -> int:
        return sum(row.content_bytes for row in (*self.states, *self.generations))


def retention_candidates(
    connection: sqlite3.Connection,
    *,
    limits: RepositoryLimits,
    now_us: int,
    dry_run: bool,
) -> RetentionVictims:
    leases.delete_expired(connection, now_us)
    generations = _generation_records(connection)
    states = _state_records(connection)
    active = {
        (str(row[0]), str(row[1]), str(row[2]))
        for row in connection.execute("SELECT kind, artifact_key, instance FROM artifact_leases")
    }
    kept_generations = _kept_generations(connection, generations, active, limits)
    generation_victims = tuple(
        record
        for record, _accessed, _metadata_bytes in generations
        if (record.identity_key, record.instance) not in kept_generations
    )
    kept_states = _kept_states(
        connection,
        states,
        active,
        kept_generations,
        limits,
    )
    state_victims = tuple(
        record
        for record, _accessed, _metadata_bytes in states
        if (record.state_key, record.instance) not in kept_states
    )
    victims = RetentionVictims(state_victims, generation_victims)
    if not dry_run and not victims.states and not victims.generations:
        _prune_producers(connection, limits, active)
    return victims


def apply_retention(
    connection: sqlite3.Connection,
    *,
    candidates: RetentionVictims,
    retired_states: Mapping[tuple[str, str], tuple[str, int]],
    retired_generations: Mapping[tuple[str, str], tuple[str, int]],
    limits: RepositoryLimits,
    now_us: int,
) -> RetentionVictims:
    current = retention_candidates(
        connection,
        limits=limits,
        now_us=now_us,
        dry_run=True,
    )
    current_states = {(row.state_key, row.instance): row for row in current.states}
    current_generations = {(row.identity_key, row.instance): row for row in current.generations}
    victims = RetentionVictims(
        tuple(
            row
            for row in candidates.states
            if current_states.get((row.state_key, row.instance)) == row
        ),
        tuple(
            row
            for row in candidates.generations
            if current_generations.get((row.identity_key, row.instance)) == row
        ),
    )
    retired = (
        *(
            retired_states[(row.state_key, row.instance)]
            for row in victims.states
            if (row.state_key, row.instance) in retired_states
        ),
        *(
            retired_generations[(row.identity_key, row.instance)]
            for row in victims.generations
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
        ((row.identity_key, row.instance) for row in victims.generations),
    )
    connection.executemany(
        "DELETE FROM generations WHERE identity_key = ? AND instance = ?",
        ((row.identity_key, row.instance) for row in victims.generations),
    )
    connection.executemany(
        """
        UPDATE state_scopes SET current_instance = NULL
        WHERE state_key = ? AND current_instance = ?
        """,
        ((row.state_key, row.instance) for row in victims.states),
    )
    connection.executemany(
        "DELETE FROM prepared_states WHERE state_key = ? AND instance = ?",
        ((row.state_key, row.instance) for row in victims.states),
    )
    connection.execute(
        """
        DELETE FROM identities
        WHERE current_instance IS NULL
          AND NOT EXISTS (
            SELECT 1 FROM generations WHERE generations.identity_key = identities.identity_key
          )
        """
    )
    connection.execute(
        """
        DELETE FROM state_scopes
        WHERE current_instance IS NULL
          AND NOT EXISTS (
            SELECT 1 FROM prepared_states
            WHERE prepared_states.state_key = state_scopes.state_key
          )
        """
    )
    active = {
        (str(row[0]), str(row[1]), str(row[2]))
        for row in connection.execute("SELECT kind, artifact_key, instance FROM artifact_leases")
    }
    _prune_producers(connection, limits, active)
    return victims


def _kept_generations(
    connection: sqlite3.Connection,
    rows: tuple[tuple[GenerationRow, int, int], ...],
    active: set[tuple[str, str, str]],
    limits: RepositoryLimits,
) -> set[tuple[str, str]]:
    active_keys = {(key, instance) for kind, key, instance in active if kind == "generation"}
    active_identities = {key for key, _instance in active_keys}
    identity_rows = connection.execute(
        """
        SELECT identity_key, current_instance
        FROM identities
        ORDER BY touched_at_us DESC, identity_key DESC
        """
    ).fetchall()
    kept_identities = set(active_identities)
    for identity_key, _current in identity_rows:
        if len(kept_identities) >= limits.retained_identities:
            break
        kept_identities.add(str(identity_key))
    pinned = set(active_keys)
    for identity_key, current in identity_rows:
        if str(identity_key) in kept_identities and current is not None:
            pinned.add((str(identity_key), str(current)))
    pinned_bytes = sum(
        record.content_bytes
        for record, _accessed, _metadata in rows
        if (record.identity_key, record.instance) in pinned
    )
    pinned_metadata = sum(
        metadata
        for record, _accessed, metadata in rows
        if (record.identity_key, record.instance) in pinned
    )
    kept = set(pinned)
    per_identity = Counter(identity for identity, _instance in kept)
    used_bytes = pinned_bytes
    used_metadata = pinned_metadata
    for record, _accessed, metadata in rows:
        key = (record.identity_key, record.instance)
        if key in kept or record.identity_key not in kept_identities:
            continue
        if len(kept) >= limits.retained_generations:
            continue
        if per_identity[record.identity_key] >= limits.retained_generations_per_identity:
            continue
        if used_bytes + record.content_bytes > limits.generation_bytes:
            continue
        if used_metadata + metadata > limits.metadata_bytes:
            continue
        kept.add(key)
        per_identity[record.identity_key] += 1
        used_bytes += record.content_bytes
        used_metadata += metadata
    return kept


def _kept_states(
    connection: sqlite3.Connection,
    rows: tuple[tuple[StateRow, int, int], ...],
    active: set[tuple[str, str, str]],
    kept_generations: set[tuple[str, str]],
    limits: RepositoryLimits,
) -> set[tuple[str, str]]:
    pinned = {(key, instance) for kind, key, instance in active if kind == "state"}
    if kept_generations:
        clauses = " OR ".join(
            "(identity_key = ? AND generation_instance = ?)" for _ in kept_generations
        )
        parameters = tuple(value for key in kept_generations for value in key)
        pinned.update(
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                f"SELECT state_key, state_instance FROM generation_states WHERE {clauses}",
                parameters,
            )
        )
    used_bytes = sum(
        record.content_bytes
        for record, _accessed, _metadata in rows
        if (record.state_key, record.instance) in pinned
    )
    kept = set(pinned)
    for record, _accessed, _metadata in rows:
        key = (record.state_key, record.instance)
        if key in kept:
            continue
        if len(kept) >= limits.retained_prepared_states:
            continue
        if used_bytes + record.content_bytes > limits.prepared_state_bytes:
            continue
        kept.add(key)
        used_bytes += record.content_bytes
    return kept


def _prune_producers(
    connection: sqlite3.Connection,
    limits: RepositoryLimits,
    active: set[tuple[str, str, str]],
) -> None:
    active_producers = {
        str(row[0])
        for row in connection.execute(
            """
            SELECT DISTINCT producer_sha256 FROM identities
            WHERE identity_key IN (
                SELECT artifact_key FROM artifact_leases WHERE kind = 'generation'
            )
            UNION
            SELECT DISTINCT producer_sha256 FROM state_scopes
            WHERE state_key IN (
                SELECT artifact_key FROM artifact_leases WHERE kind = 'state'
            )
            """
        )
    }
    rows = connection.execute(
        """
        SELECT producer_sha256 FROM producers
        WHERE NOT EXISTS (
            SELECT 1 FROM identities
            WHERE identities.producer_sha256 = producers.producer_sha256
        ) AND NOT EXISTS (
            SELECT 1 FROM state_scopes
            WHERE state_scopes.producer_sha256 = producers.producer_sha256
        )
        ORDER BY touched_at_us DESC, producer_sha256 DESC
        """
    ).fetchall()
    kept = set(active_producers)
    for row in rows:
        if len(kept) >= limits.retained_producers:
            break
        kept.add(str(row[0]))
    connection.executemany(
        "DELETE FROM producers WHERE producer_sha256 = ?",
        ((str(row[0]),) for row in rows if str(row[0]) not in kept),
    )


def _generation_records(
    connection: sqlite3.Connection,
) -> tuple[tuple[GenerationRow, int, int], ...]:
    rows = connection.execute(
        """
        SELECT i.identity_key, i.producer_sha256, i.output_plan_sha256,
               i.spec_sha256, g.instance, g.metadata_json, g.metadata_bytes,
               g.captured_observation_revision, g.content_bytes,
               g.accessed_at_us
        FROM identities AS i
        JOIN generations AS g ON g.identity_key = i.identity_key
        ORDER BY g.accessed_at_us DESC, g.created_at_us DESC, g.instance DESC
        """
    ).fetchall()
    return tuple((generation_row(row[:9]), integer(row[9]), integer(row[6])) for row in rows)


def _state_records(
    connection: sqlite3.Connection,
) -> tuple[tuple[StateRow, int, int], ...]:
    rows = connection.execute(
        """
        SELECT s.state_key, s.producer_sha256, s.output_plan_sha256,
               s.state_fingerprint, p.instance, p.metadata_json, p.metadata_bytes,
               p.content_bytes, p.accessed_at_us
        FROM state_scopes AS s
        JOIN prepared_states AS p ON p.state_key = s.state_key
        ORDER BY p.accessed_at_us DESC, p.created_at_us DESC, p.instance DESC
        """
    ).fetchall()
    return tuple((state_row(row[:8]), integer(row[8]), integer(row[6])) for row in rows)


__all__ = ["RetentionVictims", "apply_retention", "retention_candidates"]
