from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from marimo_export._diagnostics import record_cleanup_failure
from marimo_export._json import JsonValue
from marimo_export._repository.files import safe_member
from marimo_export._repository.leases import ArtifactLease, LeaseManager
from marimo_export._repository.models import (
    ExportGenerationRecord,
    PreparedStateRecord,
    RepositoryFenceError,
    RepositoryIdentity,
)

if TYPE_CHECKING:
    from marimo_export._repository.preparation import PreparationRepository


class PreparedState:
    """A leased immutable output artifact for one prepared state."""

    __slots__ = ("_lease", "_record")

    def __init__(self, record: PreparedStateRecord, lease: ArtifactLease) -> None:
        self._record = record
        self._lease = lease

    @property
    def producer_sha256(self) -> str:
        return self._record.producer_sha256

    @property
    def output_plan_sha256(self) -> str:
        return self._record.output_plan_sha256

    @property
    def state_fingerprint(self) -> str:
        return self._record.state_fingerprint

    @property
    def instance(self) -> str:
        return self._record.instance

    @property
    def path(self) -> Path:
        return self._record.path

    @property
    def metadata(self) -> Mapping[str, JsonValue]:
        return self._record.metadata

    @property
    def content_bytes(self) -> int:
        return self._record.content_bytes

    @property
    def alive(self) -> bool:
        return self._lease.alive

    def asset(self, relative: str) -> Path | None:
        if not self._lease.renew():
            return None
        return safe_member(self.path, self._record.files, relative)

    def retain(self) -> ArtifactLease:
        return self._lease.retain()

    def detach(self) -> ArtifactLease:
        return self._lease.detach()

    def close(self) -> None:
        self._lease.close()

    def __enter__(self) -> PreparedState:
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()


class PreparedExportArtifact:
    """A leased immutable notebook export prepared for consumers."""

    __slots__ = ("_files", "_lease", "_record")

    def __init__(
        self,
        record: ExportGenerationRecord,
        files: frozenset[str],
        lease: ArtifactLease,
    ) -> None:
        self._record = record
        self._files = files
        self._lease = lease

    @property
    def identity(self) -> RepositoryIdentity:
        return self._record.identity

    @property
    def instance(self) -> str:
        return self._record.instance

    @property
    def path(self) -> Path:
        return self._record.path

    @property
    def state_fingerprints(self) -> tuple[str, ...]:
        return self._record.state_fingerprints

    @property
    def captured_observation_revision(self) -> int:
        return self._record.captured_observation_revision

    @property
    def content_bytes(self) -> int:
        return self._record.content_bytes

    @property
    def alive(self) -> bool:
        return self._lease.alive

    def asset(self, relative: str) -> Path | None:
        if not self._lease.renew():
            return None
        return safe_member(self.path, self._files, relative)

    def retain(self) -> ArtifactLease:
        return self._lease.retain()

    def detach(self) -> ArtifactLease:
        return self._lease.detach()

    def close(self) -> None:
        self._lease.close()

    def __enter__(self) -> PreparedExportArtifact:
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()


class StagedPreparedState:
    """An owned staging directory for one reusable prepared state."""

    __slots__ = (
        "_closed",
        "_output_plan_sha256",
        "_producer_sha256",
        "_repository",
        "_reservation",
        "_state_fingerprint",
        "path",
    )

    def __init__(
        self,
        repository: PreparationRepository,
        path: Path,
        *,
        producer_sha256: str,
        output_plan_sha256: str,
        state_fingerprint: str,
        reservation: PreparationReservation,
    ) -> None:
        self._repository = repository
        self.path = path
        self._producer_sha256 = producer_sha256
        self._output_plan_sha256 = output_plan_sha256
        self._state_fingerprint = state_fingerprint
        self._reservation = reservation
        self._closed = False

    def commit(
        self,
        *,
        metadata: Mapping[str, object],
        replacing_instance: str | None = None,
    ) -> PreparedState:
        if self._closed:
            raise RuntimeError("The prepared state staging directory is closed")
        try:
            if not self._reservation.alive:
                raise RepositoryFenceError("The preparation reservation is stale.")
            result = self._repository._commit_prepared_state(
                self,
                metadata=metadata,
                replacing_instance=replacing_instance,
            )
        except BaseException as error:
            try:
                self.close()
            except BaseException as cleanup_error:
                record_cleanup_failure(
                    error,
                    "prepared state staging cleanup",
                    cleanup_error,
                )
            raise
        with suppress(BaseException):
            self.close()
        return result

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._repository._discard_staging(self.path)

    def __enter__(self) -> StagedPreparedState:
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()


class StagedExport:
    """An owned staging directory for one exact prepared notebook export."""

    __slots__ = ("_closed", "_identity", "_repository", "_reservation", "path")

    def __init__(
        self,
        repository: PreparationRepository,
        path: Path,
        identity: RepositoryIdentity,
        reservation: PreparationReservation,
    ) -> None:
        self._repository = repository
        self.path = path
        self._identity = identity
        self._reservation = reservation
        self._closed = False

    def commit(
        self,
        *,
        states: Sequence[PreparedState],
        captured_observation_revision: int,
        replacing_instance: str | None = None,
        commit_guard: Callable[[], None] | None = None,
    ) -> PreparedExportArtifact:
        if self._closed:
            raise RuntimeError("The export staging directory is closed")
        try:
            if not self._reservation.alive:
                raise RepositoryFenceError("The preparation reservation is stale.")
            result = self._repository._commit_export(
                self,
                states=states,
                captured_observation_revision=captured_observation_revision,
                replacing_instance=replacing_instance,
                commit_guard=commit_guard,
            )
        except BaseException as error:
            try:
                self.close()
            except BaseException as cleanup_error:
                record_cleanup_failure(
                    error,
                    "export staging cleanup",
                    cleanup_error,
                )
            raise
        with suppress(BaseException):
            self.close()
        return result

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._repository._discard_staging(self.path)

    def __enter__(self) -> StagedExport:
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()


class PreparationReservation:
    """Exclusive renewable right to prepare one exact export identity."""

    __slots__ = (
        "_closed",
        "_identity",
        "_identity_key",
        "_manager",
        "_operation_timeout_seconds",
        "fence",
    )

    def __init__(
        self,
        manager: LeaseManager,
        identity: RepositoryIdentity,
        fence: int,
        operation_timeout_seconds: float,
    ) -> None:
        self._manager = manager
        self._identity = identity
        self._identity_key = identity.key
        self.fence = fence
        self._operation_timeout_seconds = operation_timeout_seconds
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._manager.release_reservation(self._identity_key)

    @property
    def identity_key(self) -> str:
        return self._identity_key

    @property
    def owner(self) -> str:
        return self._manager.owner

    @property
    def identity(self) -> RepositoryIdentity:
        return self._identity

    @property
    def alive(self) -> bool:
        return self._manager.reservation_alive(self._identity_key, self.fence)

    @property
    def operation_timeout_seconds(self) -> float:
        return self._operation_timeout_seconds

    def __enter__(self) -> PreparationReservation:
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()


__all__ = [
    "PreparationReservation",
    "PreparedExportArtifact",
    "PreparedState",
    "StagedExport",
    "StagedPreparedState",
]
