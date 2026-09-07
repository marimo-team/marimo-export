from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from marimo_export._repository.models import (
    ObservationSnapshot,
    ObservedState,
    RepositoryLimitError,
    RepositoryLimits,
    digest,
    positive_integer,
)
from marimo_export._repository.sqlite.observations import input_names_bytes

if TYPE_CHECKING:
    from marimo_export._repository.sqlite.catalog import SqliteCatalog
    from marimo_export.repository import ExportRepository


class ObservationRepository:
    """Private raw observation persistence used by ledgers and preparation."""

    def __init__(
        self,
        *,
        catalog: SqliteCatalog,
        limits: RepositoryLimits,
        require_open: Callable[[], None],
    ) -> None:
        self._catalog = catalog
        self._limits = limits
        self._require_open = require_open

    def record(
        self,
        *,
        producer_sha256: str,
        values: Mapping[str, object],
        occurrences: int = 1,
    ) -> ObservedState:
        self._require_open()
        digest(producer_sha256, "producer_sha256")
        count = positive_integer(occurrences, "occurrences")
        initial = ObservedState(producer_sha256=producer_sha256, revision=0, values=values)
        if initial.byte_count > self._limits.observation_bytes:
            raise RepositoryLimitError(
                "One observed input vector exceeds the repository byte limit."
            )
        revision = self._catalog.record_observation(
            producer_sha256=producer_sha256,
            observed=initial,
            occurrences=count,
            input_names=input_names_bytes(initial.input_names),
            now_us=_now_us(),
            limits=self._limits,
        )
        return ObservedState(
            producer_sha256=producer_sha256,
            revision=revision,
            values=initial.values,
        )

    def advance_revision(
        self,
        *,
        producer_sha256: str,
        occurrences: int = 1,
    ) -> int:
        self._require_open()
        digest(producer_sha256, "producer_sha256")
        return self._catalog.advance_observation_revision(
            producer_sha256,
            positive_integer(occurrences, "occurrences"),
            _now_us(),
        )

    def revision(self, producer_sha256: str) -> int:
        self._require_open()
        digest(producer_sha256, "producer_sha256")
        return self._catalog.observation_revision(producer_sha256)

    def clear(self, producer_sha256: str) -> int:
        self._require_open()
        digest(producer_sha256, "producer_sha256")
        return self._catalog.clear_observations(producer_sha256)

    def observations(
        self,
        *,
        producer_sha256: str,
        inputs: tuple[str, ...],
    ) -> tuple[ObservedState, ...]:
        self._require_open()
        digest(producer_sha256, "producer_sha256")
        return self._catalog.observations(
            producer_sha256,
            input_names_bytes(_input_names(inputs)),
        )

    def latest(
        self,
        *,
        producer_sha256: str,
        inputs: tuple[str, ...],
        through_revision: int | None = None,
    ) -> ObservedState | None:
        self._require_open()
        digest(producer_sha256, "producer_sha256")
        return self._catalog.latest_observation(
            producer_sha256,
            input_names_bytes(_input_names(inputs)),
            through_revision,
        )

    def snapshot(self, producer_sha256: str) -> ObservationSnapshot:
        self._require_open()
        digest(producer_sha256, "producer_sha256")
        return self._catalog.observation_snapshot(producer_sha256)


def observation_repository(repository: ExportRepository) -> ObservationRepository:
    if not hasattr(repository, "_observations"):
        raise TypeError("repository must be an ExportRepository")
    return repository._observations


def _input_names(values: tuple[str, ...]) -> tuple[str, ...]:
    if values != tuple(sorted(set(values))) or any(not isinstance(value, str) for value in values):
        raise ValueError("inputs must be a sorted unique tuple of strings")
    return values


def _now_us() -> int:
    return time.time_ns() // 1000


__all__ = ["ObservationRepository", "observation_repository"]
