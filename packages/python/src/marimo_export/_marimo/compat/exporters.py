"""Read the attached marimo kernel for exporter preparation."""

from __future__ import annotations

import threading
import weakref
from collections.abc import Callable, Mapping
from types import CodeType
from typing import TypeVar, cast

_T = TypeVar("_T")

_STATE_LOCK = threading.Lock()
_PROCESS_STATE: dict[str, object] = {}
_KERNEL_STATE: weakref.WeakKeyDictionary[object, dict[str, object]] = weakref.WeakKeyDictionary()


def kernel_globals() -> Mapping[str, object]:
    """Return the attached kernel's global namespace, or an empty mapping."""

    from marimo._runtime.context import safe_get_context

    context = safe_get_context()
    return {} if context is None else context.globals


def kernel_state(key: str, factory: Callable[[], _T]) -> _T:
    """Return the value stored under ``key`` for the attached kernel.

    The first call for a kernel stores ``factory()``. Calls outside a kernel
    share one process-wide value.
    """

    from marimo._runtime.context import safe_get_context

    context = safe_get_context()
    with _STATE_LOCK:
        if context is None:
            states = _PROCESS_STATE
        else:
            # A kernel context shares this registry with its embedded app
            # contexts, so the weak key scopes state to one kernel.
            states = _KERNEL_STATE.setdefault(context.app_kernel_runner_registry, {})
        if key not in states:
            states[key] = factory()
        return cast(_T, states[key])


def code_sha256(code: CodeType) -> str:
    """Return marimo's SHA-256 digest of a code object's constants, names, and bytecode."""

    from marimo._save.hash import hash_module

    return hash_module(code).hex()


__all__ = ["code_sha256", "kernel_globals", "kernel_state"]
