"""Coordinate mutable application publications over immutable prepared exports."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, TypeAlias, TypeVar

from marimo_export.planning import ExportPlan
from marimo_export.prepared import PreparedAsset, PreparedExport
from marimo_export.repository import ExportRepository

if TYPE_CHECKING:
    from marimo_export._publication import PublicationControllerState

KeyT = TypeVar("KeyT", bound=Hashable)
MetadataT = TypeVar("MetadataT")


@dataclass(frozen=True, slots=True)
class PreparedPublicationCandidate(Generic[MetadataT]):
    """Return one prepared export and application metadata from a prepare callback."""

    prepared: PreparedExport
    metadata: MetadataT


PreparePublication: TypeAlias = Callable[
    [ExportRepository, Callable[[], bool]],
    PreparedPublicationCandidate[MetadataT],
]


class PreparedPublication(Generic[KeyT, MetadataT]):
    """A controller-owned prepared export selected for one application key."""

    __slots__ = ("_key", "_metadata", "_prepared")
    _key: KeyT
    _metadata: MetadataT
    _prepared: PreparedExport

    def __init__(self) -> None:
        raise TypeError("PreparedPublication values are returned by PreparedPublicationController")

    @classmethod
    def _create(
        cls,
        key: KeyT,
        candidate: PreparedPublicationCandidate[MetadataT],
    ) -> PreparedPublication[KeyT, MetadataT]:
        self = object.__new__(cls)
        self._key = key
        self._metadata = candidate.metadata
        self._prepared = candidate.prepared
        return self

    @property
    def key(self) -> KeyT:
        return self._key

    @property
    def metadata(self) -> MetadataT:
        return self._metadata

    @property
    def identity(self) -> str:
        return self._prepared.identity

    @property
    def plan(self) -> ExportPlan:
        return self._prepared.plan

    def manifest(
        self,
        export_url: str,
        *,
        state: str | Mapping[str, object] | None = None,
        refresh_interval_ms: int | None = None,
    ) -> dict[str, object]:
        """Return the core browser manifest while this publication remains retained."""

        return dict(
            self._prepared.manifest(
                export_url,
                state=state,
                refresh_interval_ms=refresh_interval_ms,
            )
        )

    def _asset(self, relative: str) -> PreparedAsset:
        return self._prepared.asset(relative)

    def _close(self) -> None:
        self._prepared.close()


class PreparedPublicationController(Generic[KeyT, MetadataT]):
    """Retain and refresh last-good prepared exports for application-defined keys."""

    __slots__ = ("_state",)

    def __init__(
        self,
        *,
        repository: ExportRepository | None = None,
        supersession_key: Callable[[KeyT], Hashable] | None = None,
        route_key: Callable[[KeyT], Hashable] | None = None,
        route_grace_seconds: float = 60.0,
    ) -> None:
        from marimo_export._publication import PublicationControllerState

        self._state: PublicationControllerState[KeyT, MetadataT] = PublicationControllerState(
            repository=repository,
            supersession_key=supersession_key,
            route_key=route_key,
            route_grace_seconds=route_grace_seconds,
        )

    @property
    def active(self) -> bool:
        """Return whether repository, preparation, or publication state is retained."""

        return self._state.active

    @property
    def keys(self) -> tuple[KeyT, ...]:
        """Return current, preparing, and route-grace application keys."""

        return self._state.keys

    async def prepare(
        self,
        key: KeyT,
        prepare: PreparePublication[MetadataT],
    ) -> PreparedPublication[KeyT, MetadataT]:
        """Prepare and commit the last-good publication for ``key``."""

        return await self._state.prepare(key, prepare)

    def current(self, key: KeyT) -> PreparedPublication[KeyT, MetadataT] | None:
        """Return the current publication for one exact application key."""

        return self._state.current(key)

    def poll(self, key: KeyT) -> PreparedPublication[KeyT, MetadataT] | None:
        """Return the current publication and schedule observation-driven refresh."""

        return self._state.poll(key)

    def asset(
        self,
        route: Hashable,
        instance: str,
        relative: str,
    ) -> PreparedAsset | None:
        """Borrow an independently leased file from a current or retained generation."""

        return self._state.asset(route, instance, relative)

    def release(self, key: KeyT) -> None:
        """Cancel and release every publication in ``key``'s supersession group."""

        self._state.release(key)

    async def close(self) -> None:
        """Cancel preparation, release publications, and close an owned repository."""

        await self._state.close()


__all__ = [
    "PreparePublication",
    "PreparedPublication",
    "PreparedPublicationCandidate",
    "PreparedPublicationController",
]
