from __future__ import annotations

import asyncio
import threading
import time
import weakref
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING, Any, cast

from marimo._runtime.executor.lifecycles import Skip
from marimo._runtime.executor.lifecycles.cached import CachedLifecycle
from marimo._save.cache import Cache
from marimo._save.loaders.lazy import LazyLoader
from marimo._save.stubs.lazy_stub import UnhashableStub

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from marimo._save.signing import CacheSigner


_LOADER_REGISTRY_LOCK = threading.Lock()
_MANAGED_HOOKS_LOCK = threading.Lock()
_MANAGED_HOOKS: weakref.WeakSet[Any] = weakref.WeakSet()


class SequentialLazyLoader(LazyLoader):
    """Restore cache values on the kernel thread.

    Native decoders can own process-level runtimes that are unsafe to enter
    from marimo's cache workers. Preserve marimo's cache format and failure
    precedence while keeping deserialization on the state-run thread.
    """

    def _read_blobs(
        self,
        unique_keys: set[str],
        ref_type_hints: dict[str, str | None],
        return_ref: str | None,
        return_type_hint: str | None,
        blob_hash_map: dict[str, str] | None = None,
        effective_signer: CacheSigner | None = None,
    ) -> dict[str, Any]:
        from marimo._save.loaders.lazy import (
            LOGGER,
            CacheSignatureError,
            _incomplete_cache_error,
        )

        unpickled: dict[str, Any] = {}
        signature_errors: list[CacheSignatureError] = []
        missing = False
        unreadable = False
        for key in unique_keys:
            try:
                data = self.store.get(key)
                if data:
                    unpickled[key] = self._deserialize_blob(
                        key,
                        data,
                        ref_type_hints,
                        return_ref,
                        return_type_hint,
                        blob_hash_map,
                        effective_signer,
                    )
                else:
                    missing = True
            except CacheSignatureError as error:
                signature_errors.append(error)
                missing = True
            except Exception as error:
                LOGGER.warning("Failed to deserialize blob %s: %s", key, error)
                unreadable = True
        if signature_errors:
            raise signature_errors[0]
        if missing:
            raise _incomplete_cache_error(effective_signer)
        if unreadable:
            raise FileNotFoundError("Incomplete cache: a blob could not be deserialized")
        return unpickled


class CompleteCachedLifecycle(CachedLifecycle):
    """Rerun a hit when marimo could restore only an unavailable value."""

    def setup(self, cell: Any, glbls: Any) -> Any:
        decision = super().setup(cell, glbls)
        if not isinstance(decision, Skip):
            return decision
        attempt = self._attempts.get(cell.cell_id)
        if attempt is None:
            return decision
        retry = _rerun_unavailable_attempt(attempt)
        if retry is attempt:
            return decision
        self._attempts[cell.cell_id] = retry
        self._exec_starts[cell.cell_id] = time.time()
        return None


def _rerun_unavailable_attempt(attempt: Cache) -> Cache:
    if not attempt.hit or not any(
        isinstance(value, UnhashableStub) for value in attempt.defs.values()
    ):
        return attempt
    return Cache.empty(
        key=attempt.key,
        defs=set(attempt.defs),
        stateful_refs=set(attempt.stateful_refs),
    )


def add_cache_write_barrier(hooks: Any) -> None:
    from marimo._runtime.runner.hooks import Priority

    # LazyLoader starts writes during lifecycle teardown. Drain them before
    # another cell hashes the same live value.
    hooks.add_post_execution(_flush_cache_writes, Priority.EARLY)


@asynccontextmanager
async def sequential_cache_loader() -> AsyncIterator[None]:
    import marimo._runtime.executor.lifecycles.cached as cached_lifecycle
    from marimo._save.loaders import PERSISTENT_LOADERS

    while not _LOADER_REGISTRY_LOCK.acquire(blocking=False):
        await asyncio.sleep(0.001)
    previous: Any = None
    previous_lifecycle: Any = None
    replaced = False
    try:
        previous = PERSISTENT_LOADERS["lazy"]
        previous_lifecycle = cached_lifecycle.CachedLifecycle
        PERSISTENT_LOADERS["lazy"] = _sequential_loader_entry(previous)
        cast(Any, cached_lifecycle).CachedLifecycle = CompleteCachedLifecycle
        replaced = True
        try:
            yield
        finally:
            if replaced:
                PERSISTENT_LOADERS["lazy"] = previous
                cast(Any, cached_lifecycle).CachedLifecycle = previous_lifecycle
    finally:
        _LOADER_REGISTRY_LOCK.release()


@contextmanager
def managed_cache_compat(hooks: Any) -> Any:
    import marimo._runtime.executor.lifecycles.cached as cached_lifecycle
    from marimo._save.loaders import PERSISTENT_LOADERS

    previous_loader = PERSISTENT_LOADERS["lazy"]
    previous_lifecycle = cached_lifecycle.CachedLifecycle
    with _MANAGED_HOOKS_LOCK:
        PERSISTENT_LOADERS["lazy"] = _sequential_loader_entry(previous_loader)
        cast(Any, cached_lifecycle).CachedLifecycle = CompleteCachedLifecycle
        if hooks not in _MANAGED_HOOKS:
            add_cache_write_barrier(hooks)
            _MANAGED_HOOKS.add(hooks)
    try:
        yield
    finally:
        with _MANAGED_HOOKS_LOCK:
            PERSISTENT_LOADERS["lazy"] = previous_loader
            cast(Any, cached_lifecycle).CachedLifecycle = previous_lifecycle


def _sequential_loader_entry(entry: Any) -> Any:
    from marimo._save.loaders import DualLoader

    if isinstance(entry, DualLoader):
        if entry.native is SequentialLazyLoader:
            return entry
        return DualLoader(native=SequentialLazyLoader, wasm=entry.wasm)
    if entry is SequentialLazyLoader:
        return entry
    return SequentialLazyLoader


def _flush_cache_writes(cell: Any, context: Any, run_result: Any) -> None:
    del cell, context, run_result
    from marimo._save.loaders import flush_active_caches

    flush_active_caches()
