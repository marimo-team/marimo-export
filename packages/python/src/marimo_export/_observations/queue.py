"""Bounded coalescing queue for repository observations."""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass

from marimo_export.observations import ObservationRejectedError, ObservedInputs

MAX_PENDING_OBSERVATIONS = 256
MAX_PENDING_BYTES = 16 * 1024 * 1024
MAX_PENDING_PRODUCERS = 32
MAX_OBSERVATION_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class PendingObservation:
    producer_sha256: str
    observed: ObservedInputs
    occurrences: int


@dataclass(frozen=True, slots=True)
class RevisionAdvance:
    producer_sha256: str
    occurrences: int


PendingWrite = PendingObservation | RevisionAdvance


class ObservationQueue:
    """Retain recent distinct vectors and every observed revision."""

    def __init__(self) -> None:
        self._pending: deque[PendingObservation] = deque()
        self._pending_bytes = 0
        self._revision_only: OrderedDict[str, int] = OrderedDict()
        self._producers: set[str] = set()

    def __bool__(self) -> bool:
        return bool(self._pending) or bool(self._revision_only)

    def add(self, producer_sha256: str, observed: ObservedInputs) -> None:
        if observed.byte_count > MAX_OBSERVATION_BYTES:
            raise ObservationRejectedError(
                f"Observed inputs exceed the {MAX_OBSERVATION_BYTES} byte limit"
            )
        if producer_sha256 not in self._producers:
            if len(self._producers) >= MAX_PENDING_PRODUCERS:
                raise ObservationRejectedError(
                    f"Observation queue already contains {MAX_PENDING_PRODUCERS} producers"
                )
            self._producers.add(producer_sha256)
        previous_index = self._latest_producer_index(producer_sha256)
        if previous_index is not None:
            previous = self._pending[previous_index]
            if previous.observed.fingerprint == observed.fingerprint:
                self._pending[previous_index] = PendingObservation(
                    producer_sha256=producer_sha256,
                    observed=observed,
                    occurrences=previous.occurrences + 1,
                )
                return
        self._pending.append(PendingObservation(producer_sha256, observed, 1))
        self._pending_bytes += observed.byte_count
        self._enforce_limits()

    def pop(self) -> PendingWrite | None:
        if self._revision_only:
            producer, occurrences = self._revision_only.popitem(last=False)
            self._remove_producer_if_empty(producer)
            return RevisionAdvance(producer, occurrences)
        if self._pending:
            pending = self._pending.popleft()
            self._pending_bytes -= pending.observed.byte_count
            self._remove_producer_if_empty(pending.producer_sha256)
            return pending
        return None

    def clear(self) -> None:
        self._pending.clear()
        self._pending_bytes = 0
        self._revision_only.clear()
        self._producers.clear()

    def _enforce_limits(self) -> None:
        while (
            len(self._pending) > MAX_PENDING_OBSERVATIONS or self._pending_bytes > MAX_PENDING_BYTES
        ):
            evicted = self._pending.popleft()
            self._pending_bytes -= evicted.observed.byte_count
            self._revision_only[evicted.producer_sha256] = (
                self._revision_only.get(evicted.producer_sha256, 0) + evicted.occurrences
            )

    def _latest_producer_index(self, producer_sha256: str) -> int | None:
        for offset, pending in enumerate(reversed(self._pending)):
            if pending.producer_sha256 == producer_sha256:
                return len(self._pending) - offset - 1
        return None

    def _remove_producer_if_empty(self, producer_sha256: str) -> None:
        if producer_sha256 in self._revision_only:
            return
        if any(pending.producer_sha256 == producer_sha256 for pending in self._pending):
            return
        self._producers.discard(producer_sha256)


__all__ = [
    "MAX_OBSERVATION_BYTES",
    "MAX_PENDING_BYTES",
    "MAX_PENDING_OBSERVATIONS",
    "MAX_PENDING_PRODUCERS",
    "ObservationQueue",
    "PendingObservation",
    "PendingWrite",
    "RevisionAdvance",
]
