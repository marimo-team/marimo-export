"""Make Marimo lazy-cache writes visible at export run boundaries."""

from __future__ import annotations

import threading
import weakref
from typing import Any

_HOOKS_LOCK = threading.Lock()
_HOOKS: weakref.WeakSet[Any] = weakref.WeakSet()


def add_cache_write_barrier(hooks: Any) -> None:
    """Install one early write barrier on a hook container."""

    from marimo._runtime.runner.hooks import Priority

    with _HOOKS_LOCK:
        if hooks in _HOOKS:
            return
        hooks.add_post_execution(_flush_cache_writes, Priority.EARLY)
        _HOOKS.add(hooks)


def flush_native_caches() -> None:
    """Wait for every active native lazy-cache write."""

    from marimo._save.loaders import flush_active_caches

    flush_active_caches()


def _flush_cache_writes(cell: Any, context: Any, run_result: Any) -> None:
    del cell, context, run_result
    flush_native_caches()


__all__ = ["add_cache_write_barrier", "flush_native_caches"]
