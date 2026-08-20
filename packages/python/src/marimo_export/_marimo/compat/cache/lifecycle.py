"""Adapt cached-cell completeness and owned-parent activity."""

from __future__ import annotations

import threading
import time
import weakref
from typing import Any

from marimo._runtime.executor.lifecycles import Skip
from marimo._runtime.executor.lifecycles.cached import CachedLifecycle

from marimo_export._marimo.compat.cache.attempts import (
    _rerun_unavailable_attempt,
    has_cache_scope,
)

_PARENT_ACTIVITY_LOCK = threading.Lock()
_PARENT_ACTIVITY: dict[
    int,
    tuple[weakref.ReferenceType[Any], dict[str, bool]],
] = {}


class CompleteCachedLifecycle(CachedLifecycle):
    """Rerun a hit when Marimo restored an unavailable exported value."""

    def setup(self, cell: Any, glbls: Any) -> Any:
        decision = super().setup(cell, glbls)
        if not has_cache_scope(self._graph):
            return decision
        if not isinstance(decision, Skip):
            _record_owned_parent_activity(self._graph, cell.cell_id, executed=True)
            return decision
        attempt = self._attempts.get(cell.cell_id)
        if attempt is None:
            _record_owned_parent_activity(self._graph, cell.cell_id, executed=False)
            return decision
        retry = _rerun_unavailable_attempt(attempt)
        if retry is attempt:
            _record_owned_parent_activity(self._graph, cell.cell_id, executed=False)
            return decision
        self._attempts[cell.cell_id] = retry
        self._exec_starts[cell.cell_id] = time.time()
        _record_owned_parent_activity(self._graph, cell.cell_id, executed=True)
        return None


def consume_parent_live_cells() -> frozenset[str]:
    """Consume cells executed live since the owned parent's prior capture."""

    from marimo._runtime.context import get_context

    return _consume_owned_parent_live_cells(get_context().graph)


def _consume_owned_parent_live_cells(graph: Any) -> frozenset[str]:
    with _PARENT_ACTIVITY_LOCK:
        entry = _PARENT_ACTIVITY.pop(id(graph), None)
    if entry is None or entry[0]() is not graph:
        return frozenset()
    activity = entry[1]
    return frozenset(cell_id for cell_id, executed in activity.items() if executed)


def _record_owned_parent_activity(graph: Any, cell_id: Any, *, executed: bool) -> None:
    from marimo._runtime.context import get_context

    from marimo_export.integration import is_owned_session

    if not is_owned_session():
        return
    context = get_context()
    if context.parent is not None:
        return
    _remember_parent_activity(graph, cell_id, executed=executed)


def _remember_parent_activity(graph: Any, cell_id: Any, *, executed: bool) -> None:
    key = id(graph)

    def discard(reference: weakref.ReferenceType[Any]) -> None:
        with _PARENT_ACTIVITY_LOCK:
            entry = _PARENT_ACTIVITY.get(key)
            if entry is not None and entry[0] is reference:
                del _PARENT_ACTIVITY[key]

    with _PARENT_ACTIVITY_LOCK:
        entry = _PARENT_ACTIVITY.get(key)
        if entry is None or entry[0]() is not graph:
            activity: dict[str, bool] = {}
            _PARENT_ACTIVITY[key] = (weakref.ref(graph, discard), activity)
        else:
            activity = entry[1]
        name = str(cell_id)
        activity[name] = activity.get(name, False) or executed


__all__ = ["CompleteCachedLifecycle", "consume_parent_live_cells"]
