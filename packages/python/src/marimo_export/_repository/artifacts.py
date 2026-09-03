from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from marimo_export._repository import artifact_access, artifact_commit, artifact_lifecycle
from marimo_export._repository.artifact_context import ArtifactContext
from marimo_export._repository.handles import (
    PreparedExportArtifact,
    PreparedState,
    StagedExport,
    StagedPreparedState,
)
from marimo_export._repository.leases import LeaseManager
from marimo_export._repository.models import (
    PruneResult,
    RepositoryIdentity,
    RepositoryLimits,
    RepositoryReservationTimeoutError,
)
from marimo_export._repository.sqlite.catalog import SqliteCatalog
from marimo_export._repository.sqlite.maintenance import maintenance_lock


class ArtifactRepository:
    """Compose immutable artifact access, commit, and lifecycle capabilities."""

    def __init__(
        self,
        root: Path,
        catalog: SqliteCatalog,
        leases: LeaseManager,
        limits: RepositoryLimits,
    ) -> None:
        self._context = ArtifactContext(root, catalog, leases, limits)
        self._local = threading.local()

    def lookup_prepared_states(
        self,
        *,
        producer_sha256: str,
        output_plan_sha256: str,
        state_fingerprints: Sequence[str],
    ) -> Mapping[str, PreparedState]:
        with self._maintenance():
            result, corrupt = artifact_access.lookup_prepared_states(
                self._context,
                producer_sha256=producer_sha256,
                output_plan_sha256=output_plan_sha256,
                state_fingerprints=state_fingerprints,
            )
            if corrupt:
                artifact_lifecycle.recover(self._context)
            return result

    def current(self, identity: RepositoryIdentity) -> PreparedExportArtifact | None:
        with self._maintenance():
            result, corrupt = artifact_access.current(self._context, identity)
            if corrupt:
                artifact_lifecycle.recover(self._context)
            return result

    def generation(
        self,
        identity: RepositoryIdentity,
        instance: str,
    ) -> PreparedExportArtifact | None:
        with self._maintenance():
            result, corrupt = artifact_access.generation(self._context, identity, instance)
            if corrupt:
                artifact_lifecycle.recover(self._context)
            return result

    def commit_prepared_state(
        self,
        staged: StagedPreparedState,
        *,
        metadata: Mapping[str, object],
        replacing_instance: str | None,
    ) -> PreparedState:
        with self._maintenance(timeout_seconds=staged._reservation.operation_timeout_seconds):
            return artifact_commit.commit_prepared_state(
                self._context,
                staged,
                metadata=metadata,
                replacing_instance=replacing_instance,
                admit=self._admit,
            )

    def commit_export(
        self,
        staged: StagedExport,
        *,
        states: Sequence[PreparedState],
        captured_observation_revision: int,
        replacing_instance: str | None,
        commit_guard: Callable[[], None] | None,
    ) -> PreparedExportArtifact:
        with self._maintenance(timeout_seconds=staged._reservation.operation_timeout_seconds):
            return artifact_commit.commit_export(
                self._context,
                staged,
                states=states,
                captured_observation_revision=captured_observation_revision,
                replacing_instance=replacing_instance,
                admit=self._admit,
                commit_guard=commit_guard,
            )

    def new_staging(self, *, timeout_seconds: float = 10.0) -> Path:
        with self._maintenance(timeout_seconds=timeout_seconds):
            return artifact_lifecycle.new_staging(
                self._context,
                timeout_seconds=timeout_seconds,
            )

    def discard_staging(self, path: Path) -> None:
        with self._maintenance():
            artifact_lifecycle.discard_owned_staging(self._context, path)

    def prune(self, *, dry_run: bool = False) -> PruneResult:
        with self._maintenance():
            return artifact_lifecycle.prune(self._context, dry_run=dry_run)

    def recover(self) -> None:
        with self._maintenance():
            artifact_lifecycle.recover(self._context)

    def retire_unindexed_artifacts(self) -> None:
        with self._maintenance():
            artifact_lifecycle.retire_unindexed_artifacts(self._context)

    def retire_catalog_snapshots(self, snapshots: tuple[Path, ...]) -> None:
        with self._maintenance():
            artifact_lifecycle.retire_catalog_snapshots(self._context, snapshots)

    def _admit(self, additional_bytes: int) -> None:
        artifact_lifecycle.admit(self._context, additional_bytes)

    @contextmanager
    def _maintenance(self, *, timeout_seconds: float = 10.0):
        depth = getattr(self._local, "depth", 0)
        if depth:
            self._local.depth = depth + 1
            try:
                yield
            finally:
                self._local.depth = depth
            return
        if timeout_seconds <= 0:
            raise RepositoryReservationTimeoutError("The export preparation reservation timed out.")
        with maintenance_lock(self._context.root, timeout_seconds=timeout_seconds):
            self._local.depth = 1
            try:
                yield
            finally:
                self._local.depth = 0


__all__ = ["ArtifactRepository"]
