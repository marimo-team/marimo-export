from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from marimo_export._repository.models import RepositoryLimits, RetentionReserve
from marimo_export._repository.sqlite import leases
from marimo_export._repository.sqlite.records import (
    GenerationRow,
    StateRow,
    generation_row,
    state_row,
)


@dataclass(slots=True)
class _Retained:
    """Artifacts that retention keeps, and the bytes they leave unspent."""

    metadata_bytes: int
    state_bytes: int
    generation_bytes: int
    generations: set[tuple[str, str]] = field(default_factory=set)
    states: set[tuple[str, str]] = field(default_factory=set)

    def keep_generation(
        self, row: GenerationRow, pinned: Iterable[StateRow], *, force: bool
    ) -> bool:
        if (row.identity_key, row.instance) in self.generations:
            return True
        return self._keep((row,), pinned, force=force)

    def keep_state(self, row: StateRow, *, force: bool) -> bool:
        return self._keep((), (row,), force=force)

    def _keep(
        self,
        generations: tuple[GenerationRow, ...],
        states: Iterable[StateRow],
        *,
        force: bool,
    ) -> bool:
        added = [row for row in states if (row.state_key, row.instance) not in self.states]
        metadata = sum(row.metadata_bytes for row in (*generations, *added))
        state_bytes = sum(row.content_bytes for row in added)
        generation_bytes = sum(row.content_bytes for row in generations)
        if not force and (
            metadata > self.metadata_bytes
            or state_bytes > self.state_bytes
            or generation_bytes > self.generation_bytes
        ):
            return False
        self.metadata_bytes -= metadata
        self.state_bytes -= state_bytes
        self.generation_bytes -= generation_bytes
        self.generations.update((row.identity_key, row.instance) for row in generations)
        self.states.update((row.state_key, row.instance) for row in added)
        return True


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
    reserve: RetentionReserve,
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
    retained = _retained(connection, generations, states, active, limits, reserve)
    generation_victims = tuple(
        row for row in generations if (row.identity_key, row.instance) not in retained.generations
    )
    state_victims = tuple(
        row for row in states if (row.state_key, row.instance) not in retained.states
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
    reserve: RetentionReserve,
    now_us: int,
) -> RetentionVictims:
    current = retention_candidates(
        connection,
        limits=limits,
        reserve=reserve,
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


def _retained(
    connection: sqlite3.Connection,
    generations: tuple[GenerationRow, ...],
    states: tuple[StateRow, ...],
    active: set[tuple[str, str, str]],
    limits: RepositoryLimits,
    reserve: RetentionReserve,
) -> _Retained:
    """Keep protected artifacts, then the most recent identities and states that fit.

    Retention spends the metadata and per-kind content budgets that admission
    checks, less the reserve for the incoming artifact. A generation is charged
    together with the states it pins. Leased artifacts, and the current
    generation of an identity under preparation, stay even when they exceed
    the budget.
    """
    retained = _Retained(
        limits.metadata_bytes - reserve.metadata_bytes,
        limits.prepared_state_bytes - reserve.state_bytes,
        limits.generation_bytes - reserve.generation_bytes,
    )
    generation_rows = {(row.identity_key, row.instance): row for row in generations}
    state_rows = {(row.state_key, row.instance): row for row in states}
    pinned: defaultdict[tuple[str, str], list[StateRow]] = defaultdict(list)
    for row in connection.execute(
        "SELECT identity_key, generation_instance, state_key, state_instance FROM generation_states"
    ):
        pinned[(str(row[0]), str(row[1]))].append(state_rows[(str(row[2]), str(row[3]))])

    for kind, key, instance in active:
        if kind == "generation" and (key, instance) in generation_rows:
            generation = generation_rows[(key, instance)]
            retained.keep_generation(generation, pinned[(key, instance)], force=True)
        elif kind == "state" and (key, instance) in state_rows:
            retained.keep_state(state_rows[(key, instance)], force=True)

    preparing = {
        str(row[0])
        for row in connection.execute("SELECT identity_key FROM preparation_reservations")
    }
    kept_identities = {key for kind, key, _instance in active if kind == "generation"}
    for row in connection.execute(
        """
        SELECT identity_key, current_instance
        FROM identities
        ORDER BY touched_at_us DESC, identity_key DESC
        """
    ).fetchall():
        identity_key = str(row[0])
        protected = identity_key in kept_identities or identity_key in preparing
        if not protected and len(kept_identities) >= limits.retained_identities:
            continue
        current = (identity_key, str(row[1]))
        if row[1] is None or retained.keep_generation(
            generation_rows[current], pinned[current], force=protected
        ):
            kept_identities.add(identity_key)

    per_identity = Counter(identity for identity, _instance in retained.generations)
    for generation in generations:
        key = (generation.identity_key, generation.instance)
        if key in retained.generations or generation.identity_key not in kept_identities:
            continue
        if len(retained.generations) >= limits.retained_generations:
            continue
        if per_identity[generation.identity_key] >= limits.retained_generations_per_identity:
            continue
        if retained.keep_generation(generation, pinned[key], force=False):
            per_identity[generation.identity_key] += 1

    for state in states:
        if len(retained.states) >= limits.retained_prepared_states:
            break
        retained.keep_state(state, force=False)
    return retained


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


def _generation_records(connection: sqlite3.Connection) -> tuple[GenerationRow, ...]:
    rows = connection.execute(
        """
        SELECT i.identity_key, i.producer_sha256, i.output_plan_sha256,
               i.spec_sha256, g.instance, g.metadata_json, g.metadata_bytes,
               g.captured_observation_revision, g.content_bytes
        FROM identities AS i
        JOIN generations AS g ON g.identity_key = i.identity_key
        ORDER BY g.accessed_at_us DESC, g.created_at_us DESC, g.instance DESC
        """
    ).fetchall()
    return tuple(generation_row(row) for row in rows)


def _state_records(connection: sqlite3.Connection) -> tuple[StateRow, ...]:
    rows = connection.execute(
        """
        SELECT s.state_key, s.producer_sha256, s.output_plan_sha256,
               s.state_fingerprint, p.instance, p.metadata_json, p.metadata_bytes,
               p.content_bytes
        FROM state_scopes AS s
        JOIN prepared_states AS p ON p.state_key = s.state_key
        ORDER BY p.accessed_at_us DESC, p.created_at_us DESC, p.instance DESC
        """
    ).fetchall()
    return tuple(state_row(row) for row in rows)


__all__ = ["RetentionVictims", "apply_retention", "retention_candidates"]
