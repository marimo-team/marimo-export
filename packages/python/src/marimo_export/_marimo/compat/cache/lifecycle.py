"""Recreate cached values that require a live Marimo session."""

from __future__ import annotations

import time
from typing import Any

from marimo._runtime.executor.lifecycles import Skip
from marimo._runtime.executor.lifecycles.cached import CachedLifecycle

from marimo_export._marimo.compat.cache.attempts import (
    _empty_attempt,
    _rerun_unavailable_attempt,
    has_cache_scope,
)


class CompleteCachedLifecycle(CachedLifecycle):
    """Rerun a hit when its restored values cannot serve the live session."""

    def setup(self, cell: Any, glbls: Any) -> Any:
        decision = super().setup(cell, glbls)
        if not has_cache_scope(self._graph):
            return decision
        if not isinstance(decision, Skip):
            return decision
        attempt = self._attempts.get(cell.cell_id)
        if attempt is None:
            return decision
        retry = (
            _empty_attempt(attempt)
            if _restored_session_state(attempt, glbls)
            else _rerun_unavailable_attempt(attempt)
        )
        if retry is attempt:
            return decision
        self._attempts[cell.cell_id] = retry
        self._exec_starts[cell.cell_id] = time.time()
        return None


def _restored_session_state(attempt: Any, glbls: Any) -> bool:
    from marimo._runtime.state import State

    return any(
        isinstance(glbls.get(name), State)
        for name in attempt.defs
        if name not in attempt.stateful_refs
    )


__all__ = ["CompleteCachedLifecycle"]
