"""Scope export cache policy and activity to one child graph."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

from marimo._save.cache import Cache
from marimo._save.stubs.lazy_stub import UnhashableStub
from marimo._save.stubs.ui_element_stub import UIElementStub

from marimo_export._marimo.capabilities import CacheActivity

CacheDisposition = Literal["hit", "miss"]


@dataclass(slots=True)
class CacheAttemptLog:
    """Mutable native cell observations for one child run."""

    output_cells: dict[Any, CacheDisposition] = field(default_factory=dict)
    authored_cells: dict[Any, CacheDisposition] = field(default_factory=dict)
    output_attempts: dict[Any, NativeCacheAttempt] = field(default_factory=dict)

    def activity(self) -> CacheActivity:
        return CacheActivity(
            authored_hits=sum(value == "hit" for value in self.authored_cells.values()),
            authored_misses=sum(value == "miss" for value in self.authored_cells.values()),
            projection_hits=sum(value == "hit" for value in self.output_cells.values()),
            projection_misses=sum(value == "miss" for value in self.output_cells.values()),
        )

    def output_attempt(self, cell_id: Any) -> NativeCacheAttempt:
        try:
            return self.output_attempts[cell_id]
        except KeyError as error:
            raise RuntimeError("output cell has no native cache attempt") from error


@dataclass(frozen=True, slots=True)
class NativeCacheAttempt:
    """Private native lookup facts retained for verified receipt extraction."""

    loader: Any
    manifest_key: str
    expected_hash: str


@dataclass(slots=True)
class _GraphScope:
    graph: Any
    output_cells: frozenset[Any]
    activity: CacheAttemptLog
    forced_cells: frozenset[Any] = frozenset()


_SCOPES_LOCK = threading.RLock()
_SCOPES: dict[int, _GraphScope] = {}


@contextmanager
def track_notebook_cache(
    child_graph: Any,
    output_cell_ids: frozenset[Any],
) -> Iterator[CacheAttemptLog]:
    """Record effective cache decisions for one exact child graph."""

    scope = _GraphScope(
        graph=child_graph,
        output_cells=output_cell_ids,
        activity=CacheAttemptLog(),
    )
    key = id(child_graph)
    with _SCOPES_LOCK:
        if key in _SCOPES:
            raise RuntimeError("cache activity is already tracked for this graph")
        _SCOPES[key] = scope
    try:
        yield scope.activity
    finally:
        with _SCOPES_LOCK:
            current = _SCOPES.get(key)
            if current is scope:
                del _SCOPES[key]


@contextmanager
def track_managed_parent_cache(graph: Any) -> Iterator[None]:
    """Activate export cache policy for one owned parent graph."""

    with track_notebook_cache(graph, frozenset()):
        yield


@contextmanager
def force_cache_misses(graph: Any, cell_ids: frozenset[Any]) -> Iterator[None]:
    """Force selected cells through native teardown for one tracked graph."""

    with _SCOPES_LOCK:
        scope = _scope_for(graph)
        if scope.forced_cells:
            raise RuntimeError("cache misses are already forced for this graph")
        scope.forced_cells = cell_ids
    try:
        yield
    finally:
        with _SCOPES_LOCK:
            current = _SCOPES.get(id(graph))
            if current is scope:
                current.forced_cells = frozenset()


def cache_attempt_wrapper(native: Callable[..., Cache]) -> Callable[..., Cache]:
    """Wrap Marimo's cache attempt without affecting untracked graphs."""

    def tracked(
        module: Any,
        graph: Any,
        cell_id: Any,
        scope_values: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> Cache:
        attempt = native(
            module,
            graph,
            cell_id,
            scope_values,
            *args,
            **kwargs,
        )
        with _SCOPES_LOCK:
            scope = _SCOPES.get(id(graph))
            if scope is None or scope.graph is not graph:
                return attempt
            attempt = _rerun_unavailable_attempt(attempt)
            if attempt.hit and cell_id in scope.forced_cells:
                attempt = _empty_attempt(attempt)
            disposition: CacheDisposition = "hit" if attempt.hit else "miss"
            target = (
                scope.activity.output_cells
                if cell_id in scope.output_cells
                else scope.activity.authored_cells
            )
            target[cell_id] = disposition
            if cell_id in scope.output_cells:
                loader = kwargs.get("loader")
                if loader is None:
                    raise RuntimeError("output cache attempt has no native loader")
                scope.activity.output_attempts[cell_id] = NativeCacheAttempt(
                    loader=loader,
                    manifest_key=str(loader.build_path(attempt.key)),
                    expected_hash=attempt.hash,
                )
            return attempt

    return tracked


def _scope_for(graph: Any) -> _GraphScope:
    scope = _SCOPES.get(id(graph))
    if scope is None or scope.graph is not graph:
        raise RuntimeError("cache misses require an active graph scope")
    return scope


def has_cache_scope(graph: Any) -> bool:
    """Return whether export cache policy owns this exact graph."""

    with _SCOPES_LOCK:
        scope = _SCOPES.get(id(graph))
        return scope is not None and scope.graph is graph


def _rerun_unavailable_attempt(attempt: Cache) -> Cache:
    if not attempt.hit or not (
        any(_contains_unavailable(value) for value in attempt.defs.values())
        or _contains_unavailable(attempt.meta.get("return"))
    ):
        return attempt
    return _empty_attempt(attempt)


def _contains_unavailable(value: object, seen: set[int] | None = None) -> bool:
    if isinstance(value, (UIElementStub, UnhashableStub)):
        return True
    if not isinstance(value, (dict, list, set, tuple)):
        return False
    if seen is None:
        seen = set()
    identity = id(value)
    if identity in seen:
        return False
    seen.add(identity)
    values = value.values() if isinstance(value, dict) else value
    return any(_contains_unavailable(item, seen) for item in values)


def _empty_attempt(attempt: Cache) -> Cache:
    return Cache.empty(
        key=attempt.key,
        defs=set(attempt.defs),
        stateful_refs=set(attempt.stateful_refs),
    )


__all__ = [
    "CacheAttemptLog",
    "NativeCacheAttempt",
    "cache_attempt_wrapper",
    "force_cache_misses",
    "has_cache_scope",
    "track_managed_parent_cache",
    "track_notebook_cache",
]
