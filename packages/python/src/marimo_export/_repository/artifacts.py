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
    AdmissionCandidate,
    PruneResult,
    RepositoryBusyError,
    RepositoryIdentity,
    RepositoryLimits,
    RepositoryWait,
)
from marimo_export._repository.sqlite.catalog import SqliteCatalog
from marimo_export._repository.sqlite.maintenance import maintenance_lock

_DISCARD_WAIT = RepositoryWait(timeout_seconds=0.0)


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
        wait: RepositoryWait,
    ) -> Mapping[str, PreparedState]:
        with self._maintenance(wait):
            result, corrupt = artifact_access.lookup_prepared_states(
                self._context,
                producer_sha256=producer_sha256,
                output_plan_sha256=output_plan_sha256,
                state_fingerprints=state_fingerprints,
            )
            if corrupt:
                artifact_lifecycle.recover(self._context)
            return result

    def current(
        self,
        identity: RepositoryIdentity,
        *,
        wait: RepositoryWait,
    ) -> PreparedExportArtifact | None:
        with self._maintenance(wait):
            result, corrupt = artifact_access.current(self._context, identity)
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
        with self._maintenance(staged._reservation.wait):
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
        with self._maintenance(staged._reservation.wait):
            return artifact_commit.commit_export(
                self._context,
                staged,
                states=states,
                captured_observation_revision=captured_observation_revision,
                replacing_instance=replacing_instance,
                admit=self._admit,
                commit_guard=commit_guard,
            )

    def new_staging(self, *, wait: RepositoryWait) -> Path:
        with self._maintenance(wait):
            return artifact_lifecycle.new_staging(
                self._context,
                timeout_seconds=wait.timeout_seconds,
            )

    def discard_staging(self, path: Path) -> None:
        try:
            with self._maintenance(_DISCARD_WAIT):
                artifact_lifecycle.discard_owned_staging(self._context, path)
        except RepositoryBusyError:
            # Recovery removes staging directories that no lease protects.
            artifact_lifecycle.abandon_owned_staging(self._context, path)

    def prune(self, *, dry_run: bool, wait: RepositoryWait) -> PruneResult:
        with self._maintenance(wait):
            return artifact_lifecycle.prune(self._context, dry_run=dry_run, candidate=None)

    def recover(self, *, wait: RepositoryWait) -> None:
        with self._maintenance(wait):
            artifact_lifecycle.recover(self._context)

    def retire_unindexed_artifacts(self, *, wait: RepositoryWait) -> None:
        with self._maintenance(wait):
            artifact_lifecycle.retire_unindexed_artifacts(self._context)

    def retire_catalog_snapshots(
        self,
        snapshots: tuple[Path, ...],
        *,
        wait: RepositoryWait,
    ) -> None:
        with self._maintenance(wait):
            artifact_lifecycle.retire_catalog_snapshots(self._context, snapshots)

    def _admit(self, candidate: AdmissionCandidate) -> None:
        artifact_lifecycle.prune(self._context, dry_run=False, candidate=candidate)

    @contextmanager
    def _maintenance(self, wait: RepositoryWait):
        depth = getattr(self._local, "depth", 0)
        if depth:
            self._local.depth = depth + 1
            try:
                yield
            finally:
                self._local.depth = depth
            return
        with maintenance_lock(self._context.root, wait):
            self._local.depth = 1
            try:
                yield
            finally:
                self._local.depth = 0


__all__ = ["ArtifactRepository"]
