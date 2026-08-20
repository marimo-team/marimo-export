from __future__ import annotations

import os
import time
import weakref
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from marimo_export._repository.artifacts import ArtifactRepository
from marimo_export._repository.leases import LeaseManager
from marimo_export._repository.models import (
    ObservedState,
    PruneResult,
    RepositoryBusyError,
    RepositoryError,
    RepositoryLimitError,
    RepositoryLimits,
    RepositoryStatus,
    RepositoryUnavailableError,
)
from marimo_export._repository.observations import ObservationRepository
from marimo_export._repository.paths import default_repository_path, private_directory
from marimo_export._repository.preparation import PreparationRepository
from marimo_export._repository.sqlite.open import open_catalog

if TYPE_CHECKING:
    from marimo_export.planning import ExportPlan
    from marimo_export.prepared import PreparedExport


class ExportRepository:
    """Store observations and verified prepared exports within bounded retention."""

    def __init__(self, root: Path, *, limits: RepositoryLimits) -> None:
        self._closed = False
        self._limits = limits
        configured = root.expanduser().absolute()
        private_directory(configured)
        self.path = private_directory(configured.resolve(strict=True))
        self._catalog, replaced_catalogs = open_catalog(self.path)
        self._leases = LeaseManager(
            self._catalog,
            ttl_seconds=limits.lease_ttl_seconds,
            heartbeat_seconds=limits.lease_heartbeat_seconds,
        )
        self._artifacts = ArtifactRepository(
            self.path,
            self._catalog,
            self._leases,
            limits,
        )
        self._preparation = PreparationRepository(self)
        self._observations = ObservationRepository(self)
        try:
            if replaced_catalogs:
                self._artifacts.retire_catalog_snapshots(replaced_catalogs)
                self._artifacts.retire_unindexed_artifacts()
            self._recover()
        except BaseException:
            self._leases.close()
            raise
        self._finalizer = weakref.finalize(self, LeaseManager.close, self._leases)

    @classmethod
    def open(
        cls,
        path: str | os.PathLike[str] | None = None,
        *,
        limits: RepositoryLimits | None = None,
    ) -> ExportRepository:
        root = cls.default_path() if path is None else Path(path)
        return cls(root, limits=limits or RepositoryLimits())

    @staticmethod
    def default_path() -> Path:
        """Return the configured repository root without touching the filesystem."""

        return default_repository_path().expanduser().absolute()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._finalizer.alive:
            self._finalizer()

    def __enter__(self) -> ExportRepository:
        self._require_open()
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()

    def record_observation(
        self,
        plan: ExportPlan,
        inputs: Mapping[str, object],
    ) -> ObservedState:
        resolved = _plan(plan)
        if set(inputs) != set(resolved.inputs):
            raise ValueError("inputs must contain the export plan inputs")
        return self._observations.record(
            producer_sha256=resolved.producer_sha256,
            values=inputs,
        )

    def observation_revision(self, plan: ExportPlan) -> int:
        return self._observations.revision(_plan(plan).producer_sha256)

    def observations(
        self,
        plan: ExportPlan,
    ) -> tuple[ObservedState, ...]:
        resolved = _plan(plan)
        return self._observations.observations(
            producer_sha256=resolved.producer_sha256,
            inputs=resolved.inputs,
        )

    def clear_observations(self, plan: ExportPlan) -> int:
        return self._observations.clear(_plan(plan).producer_sha256)

    def prepared(self, plan: ExportPlan) -> PreparedExport | None:
        """Open the exact verified prepared export for a resolved plan."""

        from marimo_export._repository.models import RepositoryIdentity
        from marimo_export.planning import ExportPlan

        self._require_open()
        if not isinstance(plan, ExportPlan):
            raise TypeError("plan must be an ExportPlan")
        artifact = self._artifacts.current(
            RepositoryIdentity(
                plan.producer_sha256,
                plan.output_plan_sha256,
                plan.spec_sha256,
            )
        )
        if artifact is None:
            return None
        try:
            matches = _artifact_matches_plan(artifact.path, plan)
        except BaseException:
            artifact.close()
            raise
        if not matches:
            artifact.close()
            return None
        from marimo_export.prepared import _exact_prepared_export

        return _exact_prepared_export(
            artifact=artifact,
            repository=self,
            owns_repository=False,
            resolve_plan=lambda: plan,
        )

    def status(self) -> RepositoryStatus:
        self._require_open()
        value = self._catalog.status(_now_us())
        return RepositoryStatus(path=self.path, **value)

    def prune(self, *, dry_run: bool = False) -> PruneResult:
        self._require_open()
        if not isinstance(dry_run, bool):
            raise TypeError("dry_run must be a boolean")
        return self._artifacts.prune(dry_run=dry_run)

    def _recover(self) -> None:
        self._require_open()
        self._artifacts.recover()

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("The export repository is closed")


def _now_us() -> int:
    return time.time_ns() // 1000


def _plan(value: object) -> ExportPlan:
    from marimo_export.planning import ExportPlan

    if not isinstance(value, ExportPlan):
        raise TypeError("plan must be an ExportPlan")
    return value


def _artifact_matches_plan(path: Path, plan: object) -> bool:
    from marimo_export.planning import ExportPlan
    from marimo_export.reader import open_export

    if not isinstance(plan, ExportPlan):
        return False
    return plan.matches(open_export(path))


__all__ = [
    "ExportRepository",
    "ObservedState",
    "PruneResult",
    "RepositoryBusyError",
    "RepositoryError",
    "RepositoryLimitError",
    "RepositoryLimits",
    "RepositoryStatus",
    "RepositoryUnavailableError",
]
