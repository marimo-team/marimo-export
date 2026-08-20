"""Own reversible process-global Marimo cache patches."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager, nullcontext
from typing import Any, cast

from marimo_export.errors import CompatibilityError

_PROCESS_PATCH_LOCK = threading.RLock()


class _CloseHandle:
    def __init__(self, callback: Callable[[], None]) -> None:
        self._callback = callback
        self._lock = threading.Lock()
        self._closed = False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self._callback()
            finally:
                self._closed = True


class _CachePatchCoordinator:
    """Reference-count one coherent replacement of Marimo cache globals."""

    def __init__(self) -> None:
        self._lock = _PROCESS_PATCH_LOCK
        self._tokens: set[object] = set()
        self._original_loader: Any = None
        self._original_lifecycle: Any = None
        self._original_attempt: Any = None
        self._loader: Any = None
        self._lifecycle: Any = None
        self._attempt: Any = None

    def open(self) -> _CloseHandle:
        import marimo._runtime.executor.lifecycles.cached as cached_lifecycle
        from marimo._save.loaders import PERSISTENT_LOADERS

        token = object()
        with self._lock:
            if self._tokens:
                self._require_installed(cached_lifecycle, PERSISTENT_LOADERS)
            else:
                from marimo_export._marimo.compat.cache.attempts import (
                    cache_attempt_wrapper,
                )
                from marimo_export._marimo.compat.cache.lifecycle import (
                    CompleteCachedLifecycle,
                )

                self._original_loader = PERSISTENT_LOADERS["lazy"]
                self._original_lifecycle = cached_lifecycle.CachedLifecycle
                self._original_attempt = cached_lifecycle.cache_attempt_from_hash
                self._loader = _sequential_loader_entry(self._original_loader)
                self._lifecycle = CompleteCachedLifecycle
                self._attempt = cache_attempt_wrapper(self._original_attempt)
                installed: list[str] = []
                try:
                    PERSISTENT_LOADERS["lazy"] = self._loader
                    installed.append("loader")
                    cast(Any, cached_lifecycle).CachedLifecycle = self._lifecycle
                    installed.append("lifecycle")
                    cast(Any, cached_lifecycle).cache_attempt_from_hash = self._attempt
                    installed.append("attempt")
                except BaseException:
                    if "attempt" in installed:
                        cast(Any, cached_lifecycle).cache_attempt_from_hash = self._original_attempt
                    if "lifecycle" in installed:
                        cast(Any, cached_lifecycle).CachedLifecycle = self._original_lifecycle
                    if "loader" in installed:
                        PERSISTENT_LOADERS["lazy"] = self._original_loader
                    self._clear()
                    raise
            self._tokens.add(token)
        return _CloseHandle(lambda: self._release(token))

    def _release(self, token: object) -> None:
        import marimo._runtime.executor.lifecycles.cached as cached_lifecycle
        from marimo._save.loaders import PERSISTENT_LOADERS

        with self._lock:
            if token not in self._tokens:
                raise RuntimeError("unbalanced cache patch release")
            owns_loader = PERSISTENT_LOADERS.get("lazy") is self._loader
            owns_lifecycle = getattr(cached_lifecycle, "CachedLifecycle", None) is self._lifecycle
            owns_attempt = (
                getattr(cached_lifecycle, "cache_attempt_from_hash", None) is self._attempt
            )
            conflict = not (owns_loader and owns_lifecycle and owns_attempt)
            self._tokens.remove(token)
            if self._tokens:
                if conflict:
                    self._raise_conflict()
                return
            if owns_loader:
                PERSISTENT_LOADERS["lazy"] = self._original_loader
            if owns_lifecycle:
                cast(Any, cached_lifecycle).CachedLifecycle = self._original_lifecycle
            if owns_attempt:
                cast(Any, cached_lifecycle).cache_attempt_from_hash = self._original_attempt
            self._clear()
            if conflict:
                self._raise_conflict()

    def _require_installed(self, cached_lifecycle: Any, loaders: Any) -> None:
        if (
            loaders.get("lazy") is not self._loader
            or getattr(cached_lifecycle, "CachedLifecycle", None) is not self._lifecycle
            or getattr(cached_lifecycle, "cache_attempt_from_hash", None) is not self._attempt
        ):
            self._raise_conflict()

    @staticmethod
    def _raise_conflict() -> None:
        raise CompatibilityError(
            "another owner replaced Marimo's cache integration while marimo-export was using it",
            code="marimo_cache_patch_conflict",
        )

    def _clear(self) -> None:
        self._original_loader = None
        self._original_lifecycle = None
        self._original_attempt = None
        self._loader = None
        self._lifecycle = None
        self._attempt = None

    def native_contract(self) -> tuple[Any, Any, Any]:
        """Return the native globals hidden by an active owned patch."""

        import marimo._runtime.executor.lifecycles.cached as cached_lifecycle
        from marimo._save.loaders import PERSISTENT_LOADERS

        with self._lock:
            if not self._tokens:
                return (
                    PERSISTENT_LOADERS.get("lazy"),
                    getattr(cached_lifecycle, "CachedLifecycle", None),
                    getattr(cached_lifecycle, "cache_attempt_from_hash", None),
                )
            self._require_installed(cached_lifecycle, PERSISTENT_LOADERS)
            return (
                self._original_loader,
                self._original_lifecycle,
                self._original_attempt,
            )


_PATCHES = _CachePatchCoordinator()
_BORROWED_RUN_LOCK = threading.Lock()


@asynccontextmanager
async def sequential_cache_loader() -> AsyncIterator[None]:
    """Own native cache adaptation while one borrowed child runs."""

    while not _BORROWED_RUN_LOCK.acquire(blocking=False):
        await asyncio.sleep(0.001)
    handle: _CloseHandle | None = None
    try:
        handle = _PATCHES.open()
        yield
    finally:
        try:
            if handle is not None:
                handle.close()
        finally:
            _BORROWED_RUN_LOCK.release()


@contextmanager
def managed_cache_compat(hooks: Any, parent_graph: Any | None = None) -> Iterator[None]:
    """Install cache behavior for one owned kernel lifespan."""

    from marimo_export._marimo.compat.cache.attempts import track_managed_parent_cache
    from marimo_export._marimo.compat.cache.barrier import add_cache_write_barrier

    scope = nullcontext() if parent_graph is None else track_managed_parent_cache(parent_graph)
    with scope:
        handle = _PATCHES.open()
        try:
            add_cache_write_barrier(hooks)
            yield
        finally:
            handle.close()


def _sequential_loader_entry(entry: Any) -> Any:
    from marimo._save.loaders import DualLoader

    from marimo_export._marimo.compat.cache.loader import SequentialLazyLoader

    if isinstance(entry, DualLoader):
        if entry.native is SequentialLazyLoader:
            return entry
        return DualLoader(native=SequentialLazyLoader, wasm=entry.wasm)
    if entry is SequentialLazyLoader:
        return entry
    return SequentialLazyLoader


def native_cache_contract() -> tuple[Any, Any, Any]:
    """Expose native cache symbols to the pinned compatibility probe."""

    return _PATCHES.native_contract()


__all__ = ["managed_cache_compat", "native_cache_contract", "sequential_cache_loader"]
