"""Record successful notebook runs through pinned private Marimo hooks."""

from __future__ import annotations

import logging
import os
import threading
import weakref
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

from marimo_export._cell_ids import canonical_cell_id
from marimo_export._json import canonical_bytes, sha256_bytes
from marimo_export._notebook import (
    _notebook_path as resolve_notebook,
)
from marimo_export._notebook import (
    _read_stable_source,
    _source_revision,
)
from marimo_export._services.identity import producer_sha256 as identify_producer
from marimo_export.observations import ObservationLedger, ObservedInputs

LOGGER = logging.getLogger(__name__)

CellSignature: TypeAlias = str
CellSignatureRow: TypeAlias = tuple[object, str, int | None, bool, bool]
SourceRevision: TypeAlias = tuple[int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class _SavedProducer:
    revision: SourceRevision
    producer_sha256: str
    signature: CellSignature


class _DocumentMatcher:
    """Cache one atomically parsed saved producer by its source revision."""

    def __init__(self, source: Path) -> None:
        self.source = source
        self._saved: _SavedProducer | None = None

    def defer(self, live_signature: CellSignature) -> tuple[str, Callable[[], str | None]]:
        revision = _source_revision(self.source)
        scope = sha256_bytes(
            canonical_bytes(
                {
                    "revision": list(revision),
                    "signature": live_signature,
                }
            )
        )

        def resolve() -> str | None:
            try:
                return self._match(revision, live_signature)
            except Exception as error:
                LOGGER.warning("Could not validate observed notebook state: %s", error)
                return None

        return scope, resolve

    def _match(
        self,
        revision: SourceRevision,
        live_signature: CellSignature,
    ) -> str | None:
        saved = self._saved
        if saved is None or saved.revision != revision:
            saved = _read_saved_producer(self.source, revision)
            if saved is None:
                return None
            self._saved = saved
        if live_signature != saved.signature:
            return None
        return saved.producer_sha256


class _KernelSourceBinding:
    """Bind one ledger to the kernel's actual authored source."""

    def __init__(self, context: object, expected: Path) -> None:
        self._context = context
        self._expected = expected

    def require_current(self) -> None:
        if not self.matches():
            raise ValueError("Marimo kernel source does not match the observation ledger source")

    def matches(self) -> bool:
        kernel = getattr(self._context, "_kernel", None)
        metadata = getattr(kernel, "app_metadata", None)
        value = getattr(metadata, "filename", None)
        if value is None:
            value = getattr(self._context, "filename", None)
        if not isinstance(value, (str, os.PathLike)):
            return False
        try:
            current = resolve_notebook(value)
        except (OSError, RuntimeError, TypeError, ValueError):
            return False
        return current == self._expected


class _HookDispatcher:
    """Own one Marimo callback and multiplex active ledger registrations."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_token = 0
        self._registrations: dict[int, Callable[[object], None]] = {}

    def register(self, callback: Callable[[object], None]) -> Callable[[], None]:
        with self._lock:
            token = self._next_token
            self._next_token += 1
            self._registrations[token] = callback
        released = False

        def release() -> None:
            nonlocal released
            with self._lock:
                if released:
                    return
                released = True
                self._registrations.pop(token, None)

        return release

    def dispatch(self, finished: object) -> None:
        with self._lock:
            callbacks = tuple(self._registrations.values())
        for callback in callbacks:
            callback(finished)


_DISPATCHERS_LOCK = threading.Lock()
_DISPATCHERS: dict[int, tuple[weakref.ReferenceType[object], _HookDispatcher]] = {}


def _dispatcher(hooks: object, priority: object) -> _HookDispatcher:
    identity = id(hooks)
    with _DISPATCHERS_LOCK:
        current = _DISPATCHERS.get(identity)
        if current is not None and current[0]() is hooks:
            return current[1]
        dispatcher = _HookDispatcher()

        def remove(reference: weakref.ReferenceType[object]) -> None:
            with _DISPATCHERS_LOCK:
                active = _DISPATCHERS.get(identity)
                if active is not None and active[0] is reference:
                    _DISPATCHERS.pop(identity, None)

        reference = weakref.ref(hooks, remove)
        _DISPATCHERS[identity] = (reference, dispatcher)
        add_on_finish = getattr(hooks, "add_on_finish", None)
        if not callable(add_on_finish):
            _DISPATCHERS.pop(identity, None)
            raise TypeError("Marimo kernel hooks cannot register an on-finish callback")
        add_on_finish(dispatcher.dispatch, priority)
        return dispatcher


def install_observation_ledger(
    context: object,
    ledger: ObservationLedger,
) -> Callable[[], None]:
    """Attach successful normal-run observation recording to one kernel."""

    if not isinstance(ledger, ObservationLedger):
        raise TypeError("ledger must be an ObservationLedger")
    from marimo._runtime.runner.hooks import Priority
    from marimo._runtime.scratch import SCRATCH_CELL_ID

    kernel = getattr(context, "_kernel", None)
    hooks = getattr(kernel, "_hooks", None)
    if kernel is None or hooks is None:
        raise TypeError("context must expose a Marimo kernel with hooks")

    binding = _KernelSourceBinding(context, ledger.source)
    binding.require_current()
    documents = _DocumentMatcher(ledger.source)
    current: list[ObservationLedger | None] = [ledger]

    def record(finished: Any) -> None:
        active = current[0]
        if active is None:
            return
        if (
            finished.interrupted
            or finished.exceptions
            or finished.cancelled_cells
            or SCRATCH_CELL_ID in finished.graph.cells
        ):
            return
        if not binding.matches():
            return
        try:
            live_signature = _live_cell_signature(kernel)
            observed = _observed_inputs(kernel)
            scope, resolve_producer = documents.defer(live_signature)
            active._record_deferred(
                observed,
                scope=scope,
                resolve_producer=resolve_producer,
            )
        except Exception as error:
            LOGGER.warning("Could not record notebook input state: %s", error)

    release_registration = _dispatcher(hooks, Priority.FINAL).register(record)
    released = False

    def release() -> None:
        nonlocal released
        if released:
            return
        released = True
        current[0] = None
        release_registration()

    return release


def _read_saved_producer(
    source: Path,
    expected_revision: SourceRevision,
) -> _SavedProducer | None:
    payload, _source_sha256, revision = _read_stable_source(source)
    if revision != expected_revision:
        return None
    cells = _saved_cell_signature(source, payload)
    producer = identify_producer(source)
    if _source_revision(source) != revision:
        return None
    return _SavedProducer(
        revision=revision,
        producer_sha256=producer,
        signature=cells,
    )


def _saved_cell_signature(source: Path, payload: bytes) -> CellSignature:
    from marimo._ast.load import load_notebook_ir
    from marimo._ast.parse import NonMarimoPythonScriptError, is_non_marimo_python_script
    from marimo._session.notebook.serializer import get_notebook_serializer

    contents = payload.decode("utf-8", errors="replace").strip()
    notebook = get_notebook_serializer(source).deserialize(
        contents,
        filepath=str(source),
    )
    if notebook and is_non_marimo_python_script(notebook):
        raise NonMarimoPythonScriptError(f"Python script {source} is not a Marimo notebook.")
    if not notebook.valid:
        raise ValueError("saved Marimo notebook is invalid")
    app = load_notebook_ir(notebook, filepath=str(source))
    app._cell_manager.ensure_one_cell()
    return _cell_signature(
        (
            cell.id,
            cell.code,
            cell.config.column,
            cell.config.disabled,
            cell.config.hide_code,
        )
        for cell in app._cell_manager.document.cells
    )


def _live_cell_signature(kernel: Any) -> CellSignature:
    return _cell_signature(
        (cell_id, cell.code, cell.config.column, cell.config.disabled, cell.config.hide_code)
        for cell_id, cell in kernel.graph.cells.items()
    )


def _cell_signature(cells: Iterable[CellSignatureRow]) -> CellSignature:
    value = [
        {
            "id": canonical_cell_id(cell_id),
            "code": code.rstrip(),
            "column": column,
            "disabled": disabled,
            "hide_code": hide_code,
        }
        for cell_id, code, column, disabled, hide_code in cells
    ]
    return sha256_bytes(canonical_bytes(value))


def _observed_inputs(kernel: object) -> ObservedInputs:
    from marimo_export._marimo.compat.inspection import observe_kernel_inputs

    observed = observe_kernel_inputs(kernel)
    return ObservedInputs(observed.values)


__all__ = ["install_observation_ledger"]
