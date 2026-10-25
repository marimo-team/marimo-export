"""Read the attached marimo kernel for exporter preparation."""

from __future__ import annotations

import threading
import weakref
from collections.abc import Mapping
from types import CodeType
from typing import TypeVar

_T = TypeVar("_T")

_STATE_LOCK = threading.Lock()
_PROCESS_STATE: dict[type, object] = {}
_KERNEL_STATE: weakref.WeakKeyDictionary[object, dict[type, object]] = weakref.WeakKeyDictionary()


def kernel_globals() -> Mapping[str, object]:
    """Return the attached kernel's global namespace, or an empty mapping."""

    from marimo._runtime.context import safe_get_context

    context = safe_get_context()
    return {} if context is None else context.globals


def kernel_state(state_type: type[_T]) -> _T:
    """Return the attached kernel's instance of ``state_type``.

    The first call for a kernel stores ``state_type()``. Calls outside a kernel
    share one process-wide instance.
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
        state = states.get(state_type)
        if not isinstance(state, state_type):
            state = states[state_type] = state_type()
        return state


def code_sha256(code: CodeType) -> str:
    """Return marimo's SHA-256 digest of a code object's constants, names, and bytecode."""

    from marimo._save.hash import hash_module

    return hash_module(code).hex()


__all__ = ["code_sha256", "kernel_globals", "kernel_state"]
