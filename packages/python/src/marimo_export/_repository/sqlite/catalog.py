from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from marimo_export._repository.capabilities import ArtifactRelease, LostLifecycle
from marimo_export._repository.models import (
    ObservationSnapshot,
    ObservedState,
    RepositoryBusyError,
    RepositoryError,
    RepositoryFenceError,
    RepositoryIdentity,
    RepositoryLimits,
    RepositoryUnavailableError,
)
from marimo_export._repository.sqlite import (
    artifacts,
    generations,
    leases,
    observations,
    prepared_states,
    recovery,
    retention,
    retired,
    status,
)
from marimo_export._repository.sqlite.records import (
    GenerationRow,
    RecoverySnapshot,
    StateRow,
)
from marimo_export._repository.sqlite.retention import RetentionVictims
from marimo_export._repository.sqlite.schema import create_schema


class SqliteCatalog:
    """Own the SQLite transaction boundary for one export repository."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._check_path()
        connection = self._connect()
        try:
            self._enable_wal(connection)
        finally:
            connection.close()
        with self.write() as connection:
            create_schema(connection)
        if os.name != "nt":
            os.chmod(self.path, 0o600)

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        try:
            connection = self._connect()
        except sqlite3.OperationalError as error:
            raise operational_error(error) from error
        try:
            yield connection
        except sqlite3.OperationalError as error:
            raise operational_error(error) from error
        finally:
            connection.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        with self._transaction("BEGIN IMMEDIATE", timeout_seconds=10) as connection:
            yield connection

    @contextmanager
    def write_timeout(self, timeout_seconds: float) -> Iterator[sqlite3.Connection]:
        with self._transaction(
            "BEGIN IMMEDIATE",
            timeout_seconds=max(0.001, timeout_seconds),
        ) as connection:
            yield connection

    @contextmanager
    def lease_write(self) -> Iterator[sqlite3.Connection]:
        with self._transaction("BEGIN IMMEDIATE", timeout_seconds=0.25) as connection:
            yield connection

    @contextmanager
    def _transaction(
        self,
        begin: str,
        *,
        timeout_seconds: float,
    ) -> Iterator[sqlite3.Connection]:
        try:
            connection = self._connect(timeout_seconds=timeout_seconds)
        except sqlite3.OperationalError as error:
            raise operational_error(error) from error
        begun = False
        try:
            connection.execute(begin)
            begun = True
            yield connection
            connection.commit()
            begun = False
        except sqlite3.OperationalError as error:
            if begun:
                connection.rollback()
            raise operational_error(error) from error
        except BaseException:
            if begun:
                connection.rollback()
            raise
        finally:
            connection.close()

    def record_observation(
        self,
        *,
        producer_sha256: str,
        observed: ObservedState,
        occurrences: int,
        input_names: bytes,
        now_us: int,
        limits: RepositoryLimits,
    ) -> int:
        with self.write() as connection:
            return observations.record_observation(
                connection,
                producer_sha256=producer_sha256,
                fingerprint=observed.fingerprint,
                input_names=input_names,
                values=observed.canonical_values,
                occurrences=occurrences,
                now_us=now_us,
                limits=limits,
            )

    def advance_observation_revision(
        self,
        producer_sha256: str,
        occurrences: int,
        now_us: int,
    ) -> int:
        with self.write() as connection:
            return observations.advance_revision(
                connection,
                producer_sha256,
                occurrences,
                now_us,
            )

    def observation_revision(self, producer_sha256: str) -> int:
        with self.read() as connection:
            return observations.observation_revision(connection, producer_sha256)

    def observations(
        self,
        producer_sha256: str,
        input_names: bytes,
    ) -> tuple[ObservedState, ...]:
        with self.write() as connection:
            result, corrupt = observations.observations(
                connection,
                producer_sha256,
                input_names,
            )
        if corrupt:
            raise RepositoryError(
                "The durable observed input relation was corrupt and was removed."
            )
        return result

    def latest_observation(
        self,
        producer_sha256: str,
        input_names: bytes,
        through_revision: int | None,
    ) -> ObservedState | None:
        with self.write() as connection:
            result, corrupt = observations.latest_observation(
                connection,
                producer_sha256,
                input_names,
                through_revision,
            )
        if corrupt:
            raise RepositoryError("The latest durable observed input was corrupt and was removed.")
        return result

    def observation_snapshot(self, producer_sha256: str) -> ObservationSnapshot:
        with self.write() as connection:
            result, corrupt = observations.observation_snapshot(connection, producer_sha256)
        if corrupt:
            raise RepositoryError("The durable observation snapshot was corrupt and was removed.")
        return result

    def clear_observations(self, producer_sha256: str) -> int:
        with self.write() as connection:
            return observations.clear_observations(connection, producer_sha256)

    def check_state_commit(
        self,
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
        timeout_seconds: float,
    ) -> None:
        with self.write_timeout(timeout_seconds) as connection:
            prepared_states.check_commit(
                connection,
                state_key=state_key,
                producer_sha256=producer_sha256,
                output_plan_sha256=output_plan_sha256,
                instance=instance,
                replacing_instance=replacing_instance,
                reservation_owner=reservation_owner,
                reservation_identity_key=reservation_identity_key,
                reservation_fence=reservation_fence,
                reservation_spec_sha256=reservation_spec_sha256,
                now_us=now_us,
            )

    def check_generation_commit(
        self,
        *,
        identity: RepositoryIdentity,
        instance: str,
        replacing_instance: str | None,
        reservation_owner: str,
        reservation_identity_key: str,
        reservation_fence: int,
        now_us: int,
        timeout_seconds: float,
    ) -> None:
        if reservation_identity_key != identity.key:
            raise RepositoryFenceError(
                "The preparation reservation belongs to another export identity."
            )
        with self.write_timeout(timeout_seconds) as connection:
            generations.check_commit(
                connection,
                identity=identity,
                instance=instance,
                replacing_instance=replacing_instance,
                reservation_owner=reservation_owner,
                reservation_identity_key=reservation_identity_key,
                reservation_fence=reservation_fence,
                now_us=now_us,
            )

    def commit_state(
        self,
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
        timeout_seconds: float,
    ) -> StateRow:
        with self.write_timeout(timeout_seconds) as connection:
            return prepared_states.commit(
                connection,
                state_key=state_key,
                producer_sha256=producer_sha256,
                output_plan_sha256=output_plan_sha256,
                state_fingerprint=state_fingerprint,
                instance=instance,
                metadata=metadata,
                content_bytes=content_bytes,
                replacing_instance=replacing_instance,
                owner=owner,
                expires_at_us=expires_at_us,
                now_us=now_us,
                limits=limits,
                reservation_owner=reservation_owner,
                reservation_identity_key=reservation_identity_key,
                reservation_fence=reservation_fence,
                reservation_spec_sha256=reservation_spec_sha256,
            )

    def current_states(
        self,
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
        with self.write() as connection:
            return prepared_states.current(
                connection,
                producer_sha256=producer_sha256,
                output_plan_sha256=output_plan_sha256,
                state_fingerprints=state_fingerprints,
                owner=owner,
                expires_at_us=expires_at_us,
                now_us=now_us,
            )

    def commit_generation(
        self,
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
        timeout_seconds: float,
    ) -> GenerationRow:
        with self.write_timeout(timeout_seconds) as connection:
            return generations.commit(
                connection,
                identity=identity,
                instance=instance,
                metadata=metadata,
                captured_observation_revision=captured_observation_revision,
                content_bytes=content_bytes,
                states=states,
                replacing_instance=replacing_instance,
                owner=owner,
                expires_at_us=expires_at_us,
                now_us=now_us,
                limits=limits,
                reservation_owner=reservation_owner,
                reservation_identity_key=reservation_identity_key,
                reservation_fence=reservation_fence,
            )

    def current_generation(
        self,
        identity: RepositoryIdentity,
        *,
        owner: str,
        expires_at_us: int,
        now_us: int,
    ) -> GenerationRow | None:
        with self.write() as connection:
            return generations.current(
                connection,
                identity,
                owner=owner,
                expires_at_us=expires_at_us,
                now_us=now_us,
            )

    def generation(
        self,
        identity: RepositoryIdentity,
        instance: str,
        *,
        owner: str,
        expires_at_us: int,
        now_us: int,
    ) -> GenerationRow | None:
        with self.write() as connection:
            return generations.by_instance(
                connection,
                identity,
                instance,
                owner=owner,
                expires_at_us=expires_at_us,
                now_us=now_us,
            )

    def all_states(self) -> tuple[StateRow, ...]:
        with self.read() as connection:
            return prepared_states.all_rows(connection)

    def all_generations(self) -> tuple[GenerationRow, ...]:
        with self.read() as connection:
            return generations.all_rows(connection)

    def remove_state(self, state: StateRow) -> None:
        with self.write() as connection:
            prepared_states.remove(connection, state)

    def remove_generation(self, generation: GenerationRow) -> None:
        with self.write() as connection:
            generations.remove(connection, generation)

    def active_artifacts(self, now_us: int) -> frozenset[tuple[str, str, str]]:
        with self.write() as connection:
            return leases.active_artifacts(connection, now_us)

    def generation_memberships(self) -> frozenset[tuple[str, str]]:
        with self.read() as connection:
            return generations.memberships(connection)

    def current_artifacts(
        self,
    ) -> tuple[frozenset[tuple[str, str]], frozenset[tuple[str, str]]]:
        with self.read() as connection:
            return artifacts.current_artifacts(connection)

    def status(self, now_us: int) -> Mapping[str, int]:
        with self.write() as connection:
            return status.repository_status(connection, now_us)

    def prune_snapshot(
        self,
        *,
        limits: RepositoryLimits,
        now_us: int,
        dry_run: bool,
    ) -> RetentionVictims:
        with self.write() as connection:
            return retention.retention_candidates(
                connection,
                limits=limits,
                now_us=now_us,
                dry_run=dry_run,
            )

    def commit_prune(
        self,
        *,
        candidates: RetentionVictims,
        retired_states: Mapping[tuple[str, str], tuple[str, int]],
        retired_generations: Mapping[tuple[str, str], tuple[str, int]],
        limits: RepositoryLimits,
        now_us: int,
    ) -> RetentionVictims:
        with self.write() as connection:
            return retention.apply_retention(
                connection,
                candidates=candidates,
                retired_states=retired_states,
                retired_generations=retired_generations,
                limits=limits,
                now_us=now_us,
            )

    def retired_artifacts(self) -> tuple[tuple[str, int | None], ...]:
        with self.write() as connection:
            return retired.all_rows(connection)

    def record_retired(self, relative_path: str, content_bytes: int, now_us: int) -> None:
        with self.write() as connection:
            retired.record(connection, relative_path, content_bytes, now_us)

    def release_retired(self, relative_path: str) -> None:
        with self.write() as connection:
            retired.release(connection, relative_path)

    def recovery_snapshot(self, now_us: int) -> RecoverySnapshot:
        with self.write() as connection:
            return recovery.snapshot(connection, now_us)

    def recover_artifacts(
        self,
        *,
        snapshot: RecoverySnapshot,
        now_us: int,
        invalid_states: Sequence[StateRow],
        invalid_generations: Sequence[GenerationRow],
        retired_states: Mapping[tuple[str, str], tuple[str, int]],
        retired_generations: Mapping[tuple[str, str], tuple[str, int]],
    ) -> tuple[tuple[StateRow, ...], tuple[GenerationRow, ...]]:
        with self.write() as connection:
            return recovery.recover_artifacts(
                connection,
                snapshot=snapshot,
                now_us=now_us,
                invalid_states=invalid_states,
                invalid_generations=invalid_generations,
                retired_states=retired_states,
                retired_generations=retired_generations,
            )

    def claim_reservation(
        self,
        owner: str,
        identity_key: str,
        producer_sha256: str,
        output_plan_sha256: str,
        spec_sha256: str,
        expires_at_us: int,
        now_us: int,
        timeout_seconds: float,
    ) -> int | None:
        with self.write_timeout(timeout_seconds) as connection:
            return leases.claim_reservation(
                connection,
                owner,
                identity_key,
                producer_sha256,
                output_plan_sha256,
                spec_sha256,
                expires_at_us,
                now_us,
            )

    def renew_lifecycle(
        self,
        *,
        owner: str,
        artifacts: Sequence[tuple[str, str, str]],
        staging: Sequence[str],
        reservations: Sequence[str],
        expires_at_us: int,
    ) -> LostLifecycle:
        with self.lease_write() as connection:
            return leases.renew_lifecycle(
                connection,
                owner=owner,
                artifacts=artifacts,
                staging=staging,
                reservations=reservations,
                expires_at_us=expires_at_us,
                now_us=time.time_ns() // 1000,
            )

    def acquire_staging(
        self,
        owner: str,
        relative_path: str,
        expires_at_us: int,
        timeout_seconds: float,
    ) -> None:
        with self.write_timeout(timeout_seconds) as connection:
            leases.acquire_staging(
                connection,
                owner,
                relative_path,
                expires_at_us,
            )

    def release_lifecycle(
        self,
        *,
        owner: str,
        artifacts: Sequence[ArtifactRelease] | None = None,
        staging: Sequence[str] | None = None,
        reservations: Sequence[str] | None = None,
    ) -> None:
        with self.lease_write() as connection:
            leases.release_lifecycle(
                connection,
                owner=owner,
                artifacts=artifacts,
                staging=staging,
                reservations=reservations,
            )

    def active_staging(self, now_us: int) -> frozenset[str]:
        with self.write() as connection:
            return leases.active_staging(connection, now_us)

    def locked(self) -> AbstractContextManager[sqlite3.Connection]:
        return self.write()

    def _connect(self, *, timeout_seconds: float = 10) -> sqlite3.Connection:
        self._check_path()
        connection = sqlite3.connect(
            self.path,
            timeout=timeout_seconds,
            isolation_level=None,
        )
        try:
            connection.execute(f"PRAGMA busy_timeout = {int(timeout_seconds * 1000)}")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA trusted_schema = OFF")
            return connection
        except BaseException:
            connection.close()
            raise

    @staticmethod
    def _enable_wal(connection: sqlite3.Connection) -> None:
        deadline = time.monotonic() + 10
        while True:
            try:
                connection.execute("PRAGMA journal_mode = WAL")
                return
            except sqlite3.OperationalError as error:
                if (
                    not any(term in str(error).lower() for term in ("locked", "busy"))
                    or time.monotonic() >= deadline
                ):
                    raise operational_error(error) from error
                time.sleep(0.01)

    def _check_path(self) -> None:
        if self.path.is_symlink():
            raise OSError(f"Export repository catalog is a symlink: {self.path}")
        if self.path.exists() and not self.path.is_file():
            raise OSError(f"Export repository catalog is invalid: {self.path}")


def operational_error(error: sqlite3.OperationalError) -> RepositoryUnavailableError:
    if any(term in str(error).lower() for term in ("locked", "busy")):
        return RepositoryBusyError("The export repository remained busy past its timeout.")
    return RepositoryUnavailableError("The export repository storage is unavailable.")


__all__ = ["SqliteCatalog", "operational_error"]
