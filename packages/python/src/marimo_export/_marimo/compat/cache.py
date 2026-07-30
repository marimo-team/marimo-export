from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, cast

from marimo._save.loaders.lazy import LazyLoader

if TYPE_CHECKING:
    from collections.abc import Iterator


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
        effective_signer: Any = None,
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


def add_cache_write_barrier(hooks: Any) -> None:
    from marimo._runtime.runner.hooks import Priority

    # LazyLoader starts writes during lifecycle teardown. Drain them before
    # another cell hashes the same live value.
    hooks.add_post_execution(_flush_cache_writes, Priority.EARLY)


@contextmanager
def sequential_cache_loader() -> Iterator[None]:
    from marimo._save.loaders import PERSISTENT_LOADERS

    previous = PERSISTENT_LOADERS["lazy"]
    PERSISTENT_LOADERS["lazy"] = _sequential_loader_entry(previous)
    try:
        yield
    finally:
        PERSISTENT_LOADERS["lazy"] = previous


def install_managed_cache_compat() -> None:
    from marimo._save.loaders import PERSISTENT_LOADERS

    PERSISTENT_LOADERS["lazy"] = _sequential_loader_entry(PERSISTENT_LOADERS["lazy"])
    _install_default_cache_write_barrier()


def _install_default_cache_write_barrier() -> None:
    from marimo._runtime.runner import hooks as hooks_module

    native = hooks_module.create_default_hooks
    if getattr(native, "__marimo_export_cache_barrier__", False):
        return

    def create_default_hooks() -> Any:
        hooks = native()
        add_cache_write_barrier(hooks)
        return hooks

    cast(Any, create_default_hooks).__marimo_export_cache_barrier__ = True
    cast(Any, hooks_module).create_default_hooks = create_default_hooks
    lifecycle = sys.modules.get("marimo._runtime.kernel_lifecycle")
    if lifecycle is not None:
        cast(Any, lifecycle).create_default_hooks = create_default_hooks


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
