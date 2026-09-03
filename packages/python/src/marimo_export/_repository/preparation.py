from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING

from marimo_export._diagnostics import record_cleanup_failure
from marimo_export._repository.handles import (
    PreparationReservation,
    PreparedExportArtifact,
    PreparedState,
    StagedExport,
    StagedPreparedState,
)
from marimo_export._repository.models import (
    ObservationSnapshot,
    ObservedState,
    RepositoryBusyError,
    RepositoryIdentity,
    RepositoryReservationTimeoutError,
    digest,
)
from marimo_export.errors import ExecutionError

if TYPE_CHECKING:
    from marimo_export.repository import ExportRepository


class PreparationRepository:
    """Private repository capabilities used while preparing exact exports."""

    def __init__(self, repository: ExportRepository) -> None:
        self._repository = repository
        self._active: ContextVar[PreparationReservation | None] = ContextVar(
            "marimo_export_preparation_reservation",
            default=None,
        )
        self._claim_guard = threading.Lock()
        self._claim_locks: dict[str, threading.Lock] = {}

    def observation_revision(self, producer_sha256: str) -> int:
        return self._repository._observations.revision(producer_sha256)

    def observation_snapshot(self, producer_sha256: str) -> ObservationSnapshot:
        return self._repository._observations.snapshot(producer_sha256)

    def observations(
        self,
        *,
        producer_sha256: str,
        inputs: tuple[str, ...],
    ) -> tuple[ObservedState, ...]:
        return self._repository._observations.observations(
            producer_sha256=producer_sha256,
            inputs=inputs,
        )

    def latest_observation(
        self,
        *,
        producer_sha256: str,
        inputs: tuple[str, ...],
        through_revision: int | None = None,
    ) -> ObservedState | None:
        return self._repository._observations.latest(
            producer_sha256=producer_sha256,
            inputs=inputs,
            through_revision=through_revision,
        )

    def lookup_prepared_states(
        self,
        *,
        producer_sha256: str,
        output_plan_sha256: str,
        state_fingerprints: Sequence[str],
    ) -> Mapping[str, PreparedState]:
        self._repository._require_open()
        digest(producer_sha256, "producer_sha256")
        digest(output_plan_sha256, "output_plan_sha256")
        for fingerprint in state_fingerprints:
            digest(fingerprint, "state_fingerprints item")
        return self._repository._artifacts.lookup_prepared_states(
            producer_sha256=producer_sha256,
            output_plan_sha256=output_plan_sha256,
            state_fingerprints=state_fingerprints,
        )

    def current(self, identity: RepositoryIdentity) -> PreparedExportArtifact | None:
        self._repository._require_open()
        _identity(identity)
        return self._repository._artifacts.current(identity)

    @contextmanager
    def reserve_preparation(
        self,
        identity: RepositoryIdentity,
        *,
        cancelled: Callable[[], bool] | None = None,
        poll_seconds: float = 0.05,
        timeout: float = 30.0,
    ) -> Iterator[PreparationReservation]:
        self._repository._require_open()
        _identity(identity)
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        deadline = time.monotonic() + timeout
        active = self._active.get()
        if active is not None:
            if active.identity.key != identity.key:
                raise RuntimeError("Nested preparation requires the active export identity")
            yield active
            return
        claim_lock = self._claim_lock(identity.key)
        while not claim_lock.acquire(
            timeout=min(poll_seconds, max(0.001, deadline - time.monotonic()))
        ):
            if cancelled is not None and cancelled():
                raise ExecutionError(
                    "export preparation was cancelled",
                    code="preparation_cancelled",
                )
            if time.monotonic() >= deadline:
                raise RepositoryReservationTimeoutError(
                    "The export preparation reservation timed out."
                )
        fence: int | None = None
        try:
            while fence is None:
                if cancelled is not None and cancelled():
                    raise ExecutionError(
                        "export preparation was cancelled",
                        code="preparation_cancelled",
                    )
                if time.monotonic() >= deadline:
                    raise RepositoryReservationTimeoutError(
                        "The export preparation reservation timed out."
                    )
                remaining = deadline - time.monotonic()
                try:
                    fence = self._repository._leases.claim_reservation(
                        identity,
                        timeout_seconds=min(poll_seconds, remaining),
                    )
                except RepositoryBusyError:
                    fence = None
                if fence is None:
                    time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
            reservation = PreparationReservation(
                self._repository._leases,
                identity,
                fence,
                timeout,
            )
            token = self._active.set(reservation)
            try:
                yield reservation
            except BaseException as error:
                try:
                    reservation.close()
                except BaseException as cleanup_error:
                    record_cleanup_failure(
                        error,
                        "preparation reservation cleanup",
                        cleanup_error,
                    )
                raise
            else:
                with suppress(BaseException):
                    reservation.close()
            finally:
                self._active.reset(token)
        finally:
            claim_lock.release()

    def stage_prepared_state(
        self,
        *,
        producer_sha256: str,
        output_plan_sha256: str,
        state_fingerprint: str,
    ) -> StagedPreparedState:
        self._repository._require_open()
        for value, label in (
            (producer_sha256, "producer_sha256"),
            (output_plan_sha256, "output_plan_sha256"),
            (state_fingerprint, "state_fingerprint"),
        ):
            digest(value, label)
        reservation = self._reservation()
        if (
            reservation.identity.producer_sha256 != producer_sha256
            or reservation.identity.output_plan_sha256 != output_plan_sha256
        ):
            raise RuntimeError("The active reservation belongs to another producer or output plan")
        return StagedPreparedState(
            self,
            self._repository._artifacts.new_staging(
                timeout_seconds=reservation.operation_timeout_seconds
            ),
            producer_sha256=producer_sha256,
            output_plan_sha256=output_plan_sha256,
            state_fingerprint=state_fingerprint,
            reservation=reservation,
        )

    def stage_export(self, identity: RepositoryIdentity) -> StagedExport:
        self._repository._require_open()
        _identity(identity)
        reservation = self._reservation()
        if reservation.identity_key != identity.key:
            raise RuntimeError("The active reservation belongs to another export identity")
        return StagedExport(
            self,
            self._repository._artifacts.new_staging(
                timeout_seconds=reservation.operation_timeout_seconds
            ),
            identity,
            reservation,
        )

    def cancellation(
        self,
        cancelled: Callable[[], bool] | None,
    ) -> Callable[[], bool]:
        reservation = self._active.get()
        if reservation is None:
            raise RuntimeError("Cancellation requires an active preparation reservation")

        def combined() -> bool:
            return (cancelled is not None and cancelled()) or not reservation.alive

        return combined

    def _commit_prepared_state(
        self,
        staged: StagedPreparedState,
        *,
        metadata: Mapping[str, object],
        replacing_instance: str | None,
    ) -> PreparedState:
        return self._repository._artifacts.commit_prepared_state(
            staged,
            metadata=metadata,
            replacing_instance=replacing_instance,
        )

    def _commit_export(
        self,
        staged: StagedExport,
        *,
        states: Sequence[PreparedState],
        captured_observation_revision: int,
        replacing_instance: str | None,
        commit_guard: Callable[[], None] | None,
    ) -> PreparedExportArtifact:
        return self._repository._artifacts.commit_export(
            staged,
            states=states,
            captured_observation_revision=captured_observation_revision,
            replacing_instance=replacing_instance,
            commit_guard=commit_guard,
        )

    def _discard_staging(self, path: Path) -> None:
        self._repository._artifacts.discard_staging(path)

    def _reservation(self) -> PreparationReservation:
        reservation = self._active.get()
        if reservation is None:
            raise RuntimeError("Export staging requires an active preparation reservation")
        if not reservation.alive:
            from marimo_export._repository.models import RepositoryFenceError

            raise RepositoryFenceError("The preparation reservation is stale.")
        return reservation

    def _claim_lock(self, identity_key: str) -> threading.Lock:
        with self._claim_guard:
            return self._claim_locks.setdefault(identity_key, threading.Lock())


def preparation_repository(repository: ExportRepository) -> PreparationRepository:
    if not hasattr(repository, "_preparation"):
        raise TypeError("repository must be an ExportRepository")
    return repository._preparation


def _identity(value: RepositoryIdentity) -> RepositoryIdentity:
    if not isinstance(value, RepositoryIdentity):
        raise TypeError("identity must be RepositoryIdentity")
    return value


__all__ = [
    "PreparationRepository",
    "PreparedExportArtifact",
    "PreparedState",
    "RepositoryIdentity",
    "StagedExport",
    "StagedPreparedState",
    "preparation_repository",
]
