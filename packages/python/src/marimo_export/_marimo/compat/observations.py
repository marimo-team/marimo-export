"""Report finished kernel runs through pinned private Marimo hooks."""

from __future__ import annotations

import logging
import os
import threading
import weakref
from collections.abc import Callable
from typing import Any

from marimo_export._cell_ids import canonical_cell_id
from marimo_export._json import json_object
from marimo_export._marimo.capabilities import KernelCell, KernelRun, RunOutcome

LOGGER = logging.getLogger(__name__)

RunCallback = Callable[[KernelRun], None]


class _HookDispatcher:
    """Own one Marimo on-finish callback and multiplex released subscriptions.

    Marimo hooks can be added but never removed, so each kernel receives one
    callback for its lifetime. The hook list holds the dispatcher, and the
    registry only references it weakly, so an unreleased subscription still
    leaves its kernel collectable with the hooks.
    """

    def __init__(self, hooks: object) -> None:
        self.hooks = weakref.ref(hooks)
        self._lock = threading.Lock()
        self._next_token = 0
        self._subscriptions: dict[int, tuple[object, object, RunCallback]] = {}

    def subscribe(
        self,
        context: object,
        kernel: object,
        callback: RunCallback,
    ) -> Callable[[], None]:
        with self._lock:
            token = self._next_token
            self._next_token += 1
            self._subscriptions[token] = (context, kernel, callback)
        released = False

        def release() -> None:
            nonlocal released
            with self._lock:
                if released:
                    return
                released = True
                self._subscriptions.pop(token, None)

        return release

    def dispatch(self, finished: Any) -> None:
        with self._lock:
            subscriptions = tuple(self._subscriptions.values())
        for context, kernel, callback in subscriptions:
            # Marimo runs finish hooks without a guard, so a raising
            # subscriber would skip later subscribers and the run's cleanup.
            try:
                callback(_kernel_run(context, kernel, finished))
            except Exception as error:
                LOGGER.warning("Could not report the finished Marimo run: %s", error)


_DISPATCHERS_LOCK = threading.Lock()
_DISPATCHERS: weakref.WeakValueDictionary[int, _HookDispatcher] = weakref.WeakValueDictionary()


def subscribe_run_finished(context: object, callback: RunCallback) -> Callable[[], None]:
    """Deliver each finished run of the context's kernel to `callback`.

    A run that is being delivered when the returned release handle is called
    can still reach `callback` once.
    """

    from marimo._runtime.runner.hooks import Priority

    kernel, hooks = _kernel_hooks(context)
    with _DISPATCHERS_LOCK:
        dispatcher = _DISPATCHERS.get(id(hooks))
        if dispatcher is None or dispatcher.hooks() is not hooks:
            add_on_finish = getattr(hooks, "add_on_finish", None)
            if not callable(add_on_finish):
                raise TypeError("Marimo kernel hooks cannot register an on-finish callback")
            dispatcher = _HookDispatcher(hooks)
            add_on_finish(dispatcher.dispatch, Priority.FINAL)
            _DISPATCHERS[id(hooks)] = dispatcher
        return dispatcher.subscribe(context, kernel, callback)


def kernel_filename(context: object) -> str | None:
    """Return the notebook path that the context's kernel currently reports."""

    kernel, _hooks = _kernel_hooks(context)
    return _reported_filename(context, kernel)


def _kernel_hooks(context: object) -> tuple[Any, object]:
    kernel = getattr(context, "_kernel", None)
    hooks = getattr(kernel, "_hooks", None)
    if kernel is None or hooks is None:
        raise TypeError("context must expose a Marimo kernel with hooks")
    return kernel, hooks


def _reported_filename(context: object, kernel: object) -> str | None:
    metadata = getattr(kernel, "app_metadata", None)
    value = getattr(metadata, "filename", None)
    if value is None:
        value = getattr(context, "filename", None)
    if isinstance(value, os.PathLike):
        value = os.fspath(value)
    return value if isinstance(value, str) else None


def _kernel_run(context: object, kernel: Any, finished: Any) -> KernelRun:
    from marimo._runtime.scratch import SCRATCH_CELL_ID

    from marimo_export._marimo.compat.inspection import observe_kernel_inputs

    def read_cells() -> tuple[KernelCell, ...]:
        return tuple(
            KernelCell(
                id=canonical_cell_id(cell_id),
                code=cell.code,
                config=json_object(cell.config.asdict(), f"cell {cell_id!s} config"),
            )
            for cell_id, cell in kernel.graph.cells.items()
        )

    return KernelRun(
        outcome=_outcome(finished),
        scratch=SCRATCH_CELL_ID in finished.graph.cells,
        filename=_reported_filename(context, kernel),
        read_cells=read_cells,
        read_inputs=lambda: observe_kernel_inputs(kernel),
    )


def _outcome(finished: Any) -> RunOutcome:
    if finished.interrupted:
        return "interrupted"
    if finished.exceptions or finished.cancelled_cells:
        return "failed"
    return "completed"


__all__ = ["kernel_filename", "subscribe_run_finished"]
