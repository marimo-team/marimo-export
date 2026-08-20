"""Bounded queue for observations awaiting saved-source validation."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from marimo_export._observations.queue import (
    MAX_OBSERVATION_BYTES,
    MAX_PENDING_BYTES,
    MAX_PENDING_OBSERVATIONS,
    MAX_PENDING_PRODUCERS,
)
from marimo_export.observations import ObservationRejectedError, ObservedInputs

ProducerResolver = Callable[[], str | None]


@dataclass(frozen=True, slots=True)
class ObservationCandidate:
    scope: str
    observed: ObservedInputs
    occurrences: int
    resolve_producer: ProducerResolver


class CandidateQueue:
    """Apply bounded backpressure until a worker resolves producer identity."""

    def __init__(self) -> None:
        self._pending: deque[ObservationCandidate] = deque()
        self._pending_bytes = 0
        self._scopes: set[str] = set()

    def __bool__(self) -> bool:
        return bool(self._pending)

    def add(
        self,
        *,
        scope: str,
        observed: ObservedInputs,
        resolve_producer: ProducerResolver,
    ) -> None:
        if not isinstance(scope, str) or not scope or len(scope.encode("utf-8")) > 1024:
            raise ValueError("observation scope must be a bounded non-empty string")
        if observed.byte_count > MAX_OBSERVATION_BYTES:
            raise ObservationRejectedError(
                f"Observed inputs exceed the {MAX_OBSERVATION_BYTES} byte limit"
            )
        if scope not in self._scopes and len(self._scopes) >= MAX_PENDING_PRODUCERS:
            raise ObservationRejectedError(
                f"Observation queue already contains {MAX_PENDING_PRODUCERS} source revisions"
            )
        previous_index = self._latest_scope_index(scope)
        if previous_index is not None:
            previous = self._pending[previous_index]
            if previous.observed.fingerprint == observed.fingerprint:
                self._pending[previous_index] = ObservationCandidate(
                    scope=scope,
                    observed=observed,
                    occurrences=previous.occurrences + 1,
                    resolve_producer=resolve_producer,
                )
                return
        if (
            len(self._pending) >= MAX_PENDING_OBSERVATIONS
            or self._pending_bytes + observed.byte_count > MAX_PENDING_BYTES
        ):
            raise ObservationRejectedError("Observation queue is full")
        self._pending.append(ObservationCandidate(scope, observed, 1, resolve_producer))
        self._pending_bytes += observed.byte_count
        self._scopes.add(scope)

    def pop(self) -> ObservationCandidate | None:
        if not self._pending:
            return None
        pending = self._pending.popleft()
        self._pending_bytes -= pending.observed.byte_count
        if not any(candidate.scope == pending.scope for candidate in self._pending):
            self._scopes.discard(pending.scope)
        return pending

    def clear(self) -> None:
        self._pending.clear()
        self._pending_bytes = 0
        self._scopes.clear()

    def _latest_scope_index(self, scope: str) -> int | None:
        for offset, candidate in enumerate(reversed(self._pending)):
            if candidate.scope == scope:
                return len(self._pending) - offset - 1
        return None


__all__ = ["CandidateQueue", "ObservationCandidate", "ProducerResolver"]
