from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from time import monotonic
from typing import Generic, TypeVar

from marimo_export.prepared import PreparedAsset
from marimo_export.publication import (
    PreparedPublication,
    PreparedPublicationCandidate,
    PreparePublication,
)
from marimo_export.repository import ExportRepository, RepositoryError

KeyT = TypeVar("KeyT", bound=Hashable)
MetadataT = TypeVar("MetadataT")


@dataclass(slots=True)
class _Work(Generic[KeyT, MetadataT]):
    key: KeyT
    group: Hashable
    route: Hashable
    token: int
    prepare: PreparePublication[MetadataT]
    cancelled: threading.Event = field(default_factory=threading.Event)
    task: asyncio.Task[PreparedPublication[KeyT, MetadataT]] | None = None


@dataclass(frozen=True, slots=True)
class _OwnedPublication(Generic[KeyT, MetadataT]):
    key: KeyT
    group: Hashable
    route: Hashable
    prepare: PreparePublication[MetadataT]
    publication: PreparedPublication[KeyT, MetadataT]


@dataclass(frozen=True, slots=True)
class _RetiredPublication(Generic[KeyT, MetadataT]):
    owned: _OwnedPublication[KeyT, MetadataT]
    expires_at: float


class PublicationControllerState(Generic[KeyT, MetadataT]):
    def __init__(
        self,
        *,
        repository: ExportRepository | None,
        supersession_key: Callable[[KeyT], Hashable] | None,
        route_key: Callable[[KeyT], Hashable] | None,
        route_grace_seconds: float,
    ) -> None:
        if route_grace_seconds < 0:
            raise ValueError("route_grace_seconds must be nonnegative")
        self._repository_value = repository
        self._owns_repository = repository is None
        self._repository_lock = threading.Lock()
        self._supersession_key = supersession_key or _same_key
        self._route_key = route_key or _same_key
        self._route_grace_seconds = route_grace_seconds
        self._current: dict[KeyT, _OwnedPublication[KeyT, MetadataT]] = {}
        self._retired: list[_RetiredPublication[KeyT, MetadataT]] = []
        self._work: dict[int, _Work[KeyT, MetadataT]] = {}
        self._desired: dict[Hashable, int] = {}
        self._refresh_tasks: dict[KeyT, asyncio.Task[None]] = {}
        self._retirement_timer: asyncio.TimerHandle | None = None
        self._retirement_deadline: float | None = None
        self._next_token = 0
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    @property
    def active(self) -> bool:
        return bool(
            self._repository_value is not None or self._current or self._retired or self._work
        )

    @property
    def keys(self) -> tuple[KeyT, ...]:
        values = [
            *self._current,
            *(retired.owned.key for retired in self._retired),
            *(work.key for work in self._work.values()),
        ]
        return tuple(dict.fromkeys(values))

    async def prepare(
        self,
        key: KeyT,
        prepare: PreparePublication[MetadataT],
    ) -> PreparedPublication[KeyT, MetadataT]:
        if self._closed:
            raise RuntimeError("The prepared publication controller is closed")
        work = self._start_work(key, prepare)
        assert work.task is not None
        try:
            return await asyncio.shield(work.task)
        except asyncio.CancelledError as primary:
            work.cancelled.set()
            await _settle_cancelled(work.task)
            raise primary
        finally:
            self._work.pop(work.token, None)

    def current(self, key: KeyT) -> PreparedPublication[KeyT, MetadataT] | None:
        self._prune()
        owned = self._current.get(key)
        return None if owned is None else owned.publication

    def poll(self, key: KeyT) -> PreparedPublication[KeyT, MetadataT] | None:
        self._prune()
        owned = self._current.get(key)
        if owned is None:
            return None
        pending = any(work.group == owned.group for work in self._work.values())
        if key not in self._refresh_tasks and not pending:
            task = asyncio.create_task(self._refresh_if_stale(owned))
            self._refresh_tasks[key] = task
            task.add_done_callback(
                lambda completed, selected_key=key: self._refresh_finished(selected_key, completed)
            )
        return owned.publication

    def asset(
        self,
        route: Hashable,
        instance: str,
        relative: str,
    ) -> PreparedAsset | None:
        hash(route)
        self._prune()
        candidates = [
            owned.publication
            for owned in self._current.values()
            if owned.route == route and owned.publication.identity == instance
        ]
        candidates.extend(
            retired.owned.publication
            for retired in self._retired
            if retired.owned.route == route and retired.owned.publication.identity == instance
        )
        for publication in candidates:
            try:
                return publication._asset(relative)
            except RepositoryError:
                continue
        return None

    def release(self, key: KeyT) -> None:
        group = self._group(key)
        for work in self._work.values():
            if work.group == group:
                work.cancelled.set()
        for current_key, owned in tuple(self._current.items()):
            if owned.group == group:
                self._current.pop(current_key).publication._close()
        kept: list[_RetiredPublication[KeyT, MetadataT]] = []
        for retired in self._retired:
            if retired.owned.group == group:
                retired.owned.publication._close()
            else:
                kept.append(retired)
        self._retired = kept
        self._desired.pop(group, None)
        self._schedule_retirement()

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    def _start_work(
        self,
        key: KeyT,
        prepare: PreparePublication[MetadataT],
    ) -> _Work[KeyT, MetadataT]:
        hash(key)
        group = self._group(key)
        route = self._route(key)
        for pending in self._work.values():
            if pending.group == group:
                pending.cancelled.set()
        self._next_token += 1
        work = _Work(
            key=key,
            group=group,
            route=route,
            token=self._next_token,
            prepare=prepare,
        )
        self._desired[group] = work.token
        work.task = asyncio.create_task(self._produce(work))
        self._work[work.token] = work
        return work

    async def _produce(
        self,
        work: _Work[KeyT, MetadataT],
    ) -> PreparedPublication[KeyT, MetadataT]:
        candidate = await asyncio.to_thread(
            work.prepare,
            self._repository(),
            work.cancelled.is_set,
        )
        if not isinstance(candidate, PreparedPublicationCandidate):
            raise TypeError("prepare must return a PreparedPublicationCandidate")
        publication = PreparedPublication._create(work.key, candidate)
        if self._closed or work.cancelled.is_set() or self._desired.get(work.group) != work.token:
            publication._close()
            raise asyncio.CancelledError
        self._commit(work, publication)
        return publication

    def _commit(
        self,
        work: _Work[KeyT, MetadataT],
        publication: PreparedPublication[KeyT, MetadataT],
    ) -> None:
        for key, current in tuple(self._current.items()):
            if current.group != work.group:
                continue
            self._current.pop(key)
            if current.publication.identity == publication.identity and current.route == work.route:
                current.publication._close()
            else:
                self._retired.append(
                    _RetiredPublication(
                        current,
                        monotonic() + self._route_grace_seconds,
                    )
                )
        self._current[work.key] = _OwnedPublication(
            key=work.key,
            group=work.group,
            route=work.route,
            prepare=work.prepare,
            publication=publication,
        )
        self._prune()

    async def _refresh_if_stale(
        self,
        selected: _OwnedPublication[KeyT, MetadataT],
    ) -> None:
        try:
            revision = await asyncio.to_thread(
                self._repository().observation_revision,
                selected.publication.plan,
            )
            if (
                self._closed
                or self._current.get(selected.key) is not selected
                or revision <= selected.publication.plan.observation_revision
            ):
                return
            await self.prepare(selected.key, selected.prepare)
        except asyncio.CancelledError:
            return
        except Exception:
            return

    def _refresh_finished(
        self,
        key: KeyT,
        task: asyncio.Task[None],
    ) -> None:
        if self._refresh_tasks.get(key) is task:
            self._refresh_tasks.pop(key, None)

    async def _close(self) -> None:
        self._closed = True
        self._cancel_retirement_timer()
        refreshes = tuple(self._refresh_tasks.values())
        work = tuple(self._work.values())
        for item in work:
            item.cancelled.set()
        await asyncio.gather(
            *refreshes,
            *(item.task for item in work if item.task is not None),
            return_exceptions=True,
        )
        self._refresh_tasks.clear()
        self._work.clear()
        self._desired.clear()
        primary: BaseException | None = None
        for owned in self._current.values():
            try:
                owned.publication._close()
            except BaseException as error:
                primary = primary or error
        for retired in self._retired:
            try:
                retired.owned.publication._close()
            except BaseException as error:
                primary = primary or error
        self._current.clear()
        self._retired.clear()
        with self._repository_lock:
            repository = self._repository_value
            self._repository_value = None
        if repository is not None and self._owns_repository:
            try:
                await asyncio.to_thread(repository.close)
            except BaseException as error:
                primary = primary or error
        if primary is not None:
            raise primary

    def _repository(self) -> ExportRepository:
        with self._repository_lock:
            if self._closed:
                raise RuntimeError("The prepared publication controller is closed")
            if self._repository_value is None:
                self._repository_value = ExportRepository.open()
            return self._repository_value

    def _group(self, key: KeyT) -> Hashable:
        group = self._supersession_key(key)
        hash(group)
        return group

    def _route(self, key: KeyT) -> Hashable:
        route = self._route_key(key)
        hash(route)
        return route

    def _prune(self) -> None:
        now = monotonic()
        kept: list[_RetiredPublication[KeyT, MetadataT]] = []
        for retired in self._retired:
            if retired.expires_at > now:
                kept.append(retired)
            else:
                retired.owned.publication._close()
        self._retired = kept
        self._schedule_retirement()

    def _schedule_retirement(self) -> None:
        if self._closed or not self._retired:
            self._cancel_retirement_timer()
            return
        deadline = min(retired.expires_at for retired in self._retired)
        if (
            self._retirement_timer is not None
            and not self._retirement_timer.cancelled()
            and self._retirement_deadline == deadline
        ):
            return
        self._cancel_retirement_timer()
        self._retirement_deadline = deadline
        self._retirement_timer = asyncio.get_running_loop().call_later(
            max(0.0, deadline - monotonic()),
            self._retirement_due,
        )

    def _retirement_due(self) -> None:
        self._retirement_timer = None
        self._retirement_deadline = None
        if not self._closed:
            self._prune()

    def _cancel_retirement_timer(self) -> None:
        if self._retirement_timer is not None:
            self._retirement_timer.cancel()
        self._retirement_timer = None
        self._retirement_deadline = None


def _same_key(key: KeyT) -> Hashable:
    return key


async def _settle_cancelled(
    task: asyncio.Task[PreparedPublication[KeyT, MetadataT]],
) -> None:
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except BaseException:
            return
    if not task.cancelled():
        try:
            task.result()
        except BaseException:
            return


__all__ = ["PublicationControllerState"]
