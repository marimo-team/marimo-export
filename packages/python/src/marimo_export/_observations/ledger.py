"""Bounded asynchronous persistence for observed notebook inputs."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from marimo_export._format import digest
from marimo_export._notebook import _notebook_path
from marimo_export._observations.candidates import (
    CandidateQueue,
    ObservationCandidate,
    ProducerResolver,
)
from marimo_export._observations.queue import (
    ObservationQueue,
    PendingObservation,
    PendingWrite,
)
from marimo_export._observations.source import SourceProducer
from marimo_export._repository.observations import (
    ObservationRepository,
    observation_repository,
)
from marimo_export.observations import (
    ObservationPersistenceError,
    ObservedInputs,
)
from marimo_export.repository import (
    ExportRepository,
    RepositoryBusyError,
    RepositoryLimitError,
)
from marimo_export.spec import StrPath

MAX_PERSISTENCE_ATTEMPTS = 3
PERSISTENCE_RETRY_SECONDS = 0.01


@dataclass(frozen=True, slots=True)
class _OpenedObservations:
    repository: ObservationRepository
    close: Callable[[], None]


_RepositoryFactory = Callable[[], _OpenedObservations]


def _open_observations() -> _OpenedObservations:
    repository = ExportRepository.open()
    try:
        return _OpenedObservations(
            repository=observation_repository(repository),
            close=repository.close,
        )
    except BaseException:
        repository.close()
        raise


class ObservationLedger:
    """Coalesce observed inputs onto one bounded repository worker."""

    def __init__(
        self,
        source: StrPath,
        *,
        repository: ExportRepository | None = None,
        _repository_factory: _RepositoryFactory | None = None,
    ) -> None:
        if repository is not None and _repository_factory is not None:
            raise TypeError("repository and _repository_factory are mutually exclusive")
        self.source = _notebook_path(source)
        self._repository = None if repository is None else observation_repository(repository)
        self._repository_factory = _repository_factory or _open_observations
        self._owned_close: Callable[[], None] | None = None
        self._source_producer = SourceProducer(self.source)
        self._condition = threading.Condition()
        self._queue = ObservationQueue()
        self._candidates = CandidateQueue()
        self._working = False
        self._closing = False
        self._closed = False
        self._close_complete = False
        self._failure: BaseException | None = None
        self._worker = threading.Thread(
            target=self._run,
            name="marimo-export-observations",
            daemon=True,
        )
        self._worker.start()

    def __enter__(self) -> ObservationLedger:
        with self._condition:
            self._require_recording()
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()

    def record(
        self,
        observed: ObservedInputs,
        *,
        producer_sha256: str | None = None,
    ) -> None:
        if not isinstance(observed, ObservedInputs):
            raise TypeError("observed must be ObservedInputs")
        with self._condition:
            self._require_recording()
        producer = (
            self._source_producer.resolve()
            if producer_sha256 is None
            else digest(producer_sha256, "producer_sha256")
        )
        with self._condition:
            self._require_recording()
            self._queue.add(producer, observed)
            self._condition.notify()

    def _record_deferred(
        self,
        observed: ObservedInputs,
        *,
        scope: str,
        resolve_producer: ProducerResolver,
    ) -> None:
        if not isinstance(observed, ObservedInputs):
            raise TypeError("observed must be ObservedInputs")
        if not callable(resolve_producer):
            raise TypeError("resolve_producer must be callable")
        with self._condition:
            self._require_recording()
            self._candidates.add(
                scope=scope,
                observed=observed,
                resolve_producer=resolve_producer,
            )
            self._condition.notify()

    def flush(self) -> None:
        with self._condition:
            self._condition.wait_for(
                lambda: not self._queue and not self._candidates and not self._working
            )
            self._raise_failure()

    def close(self) -> None:
        with self._condition:
            if not self._closing:
                self._closing = True
                self._condition.notify_all()
        self._worker.join()
        with self._condition:
            if not self._close_complete:
                raise RuntimeError("The observation ledger worker did not close")
            self._raise_failure()

    def _run(self) -> None:
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(
                        lambda: bool(self._queue) or bool(self._candidates) or self._closing
                    )
                    pending = self._queue.pop() or self._candidates.pop()
                    if pending is None:
                        return
                    self._working = True
                failed = False
                try:
                    failed = not self._persist(pending)
                finally:
                    with self._condition:
                        self._working = False
                        if failed:
                            self._discard_pending()
                        self._condition.notify_all()
                if failed:
                    return
        except BaseException as error:
            with self._condition:
                if self._failure is None:
                    self._failure = error
                self._working = False
                self._discard_pending()
                self._condition.notify_all()
        finally:
            self._finish_close()

    def _persist(self, pending: PendingWrite | ObservationCandidate) -> bool:
        try:
            if isinstance(pending, ObservationCandidate):
                producer_sha256 = pending.resolve_producer()
                if producer_sha256 is None:
                    return True
                digest(producer_sha256, "producer_sha256")
            else:
                producer_sha256 = pending.producer_sha256
            repository = self._repository_for_write()
            if isinstance(pending, (PendingObservation, ObservationCandidate)):
                try:
                    self._retry_write(
                        lambda: repository.record(
                            producer_sha256=producer_sha256,
                            values=pending.observed.values,
                            occurrences=pending.occurrences,
                        )
                    )
                except RepositoryLimitError:
                    self._retry_write(
                        lambda: repository.advance_revision(
                            producer_sha256=producer_sha256,
                            occurrences=pending.occurrences,
                        )
                    )
            else:
                self._retry_write(
                    lambda: repository.advance_revision(
                        producer_sha256=producer_sha256,
                        occurrences=pending.occurrences,
                    )
                )
            return True
        except Exception as error:
            # The repository already classifies exhausted lock contention as
            # RepositoryBusyError. Availability and integrity failures require
            # caller intervention, so replay them without another write attempt.
            self._remember_failure(error)
            return False

    def _retry_write(self, write: Callable[[], object]) -> None:
        for attempt in range(MAX_PERSISTENCE_ATTEMPTS):
            try:
                write()
                return
            except RepositoryBusyError:
                if attempt + 1 == MAX_PERSISTENCE_ATTEMPTS:
                    raise
                time.sleep(PERSISTENCE_RETRY_SECONDS * (attempt + 1))

    def _repository_for_write(self) -> ObservationRepository:
        repository = self._repository
        if repository is not None:
            return repository
        opened = self._repository_factory()
        self._repository = opened.repository
        self._owned_close = opened.close
        return opened.repository

    def _finish_close(self) -> None:
        failure: BaseException | None = None
        owned_close = self._owned_close
        self._repository = None
        self._owned_close = None
        if owned_close is not None:
            try:
                owned_close()
            except BaseException as error:
                failure = error
        with self._condition:
            if failure is not None and self._failure is None:
                self._failure = failure
            self._closed = True
            self._close_complete = True
            self._condition.notify_all()

    def _remember_failure(self, error: BaseException) -> None:
        with self._condition:
            if self._failure is None:
                self._failure = error

    def _discard_pending(self) -> None:
        self._queue.clear()
        self._candidates.clear()

    def _raise_failure(self) -> None:
        if self._failure is not None:
            raise ObservationPersistenceError(
                "Could not persist observed notebook inputs"
            ) from self._failure

    def _require_recording(self) -> None:
        self._raise_failure()
        if self._closing or self._closed:
            raise RuntimeError("The observation ledger is closed")


__all__ = ["ObservationLedger"]
