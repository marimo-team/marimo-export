from __future__ import annotations

import gc
import logging
import threading
import weakref
from collections.abc import Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import marimo
import marimo_export._marimo.compat.inspection as inspection
import marimo_export._marimo.composition as composition
import marimo_export._observations.ledger as ledger_module
import marimo_export._observations.source as source_module
import pytest
from marimo._ast.compiler import compile_cell
from marimo._runtime.dataflow import DirectedGraph
from marimo._runtime.runner.hook_context import CancelledCells, OnFinishHookContext
from marimo._runtime.runner.hooks import NotebookCellHooks
from marimo._runtime.scratch import SCRATCH_CELL_ID
from marimo._types.ids import CellId_t
from marimo_export._marimo.capabilities import KernelRun
from marimo_export._services.identity import producer_sha256
from marimo_export.integration import KernelInputObservation
from marimo_export.observations import ObservationLedger, install_observation_ledger

_NOTEBOOK = """
import marimo

app = marimo.App()


@app.cell(hide_code=True)
def _():
    value = 1
    return (value,)


@app.cell(column=1)
def _(value):
    doubled = value * 2
    return (doubled,)


if __name__ == "__main__":
    app.run()
""".lstrip()


class _Repository:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, object], int]] = []

    def record(
        self,
        *,
        producer_sha256: str,
        values: Mapping[str, object],
        occurrences: int = 1,
    ) -> object:
        self.records.append((producer_sha256, dict(values), occurrences))
        return object()

    def advance_revision(
        self,
        *,
        producer_sha256: str,
        occurrences: int = 1,
    ) -> int:
        del producer_sha256
        return occurrences

    def close(self) -> None:
        pass


def _callback(hooks: NotebookCellHooks) -> Callable[[OnFinishHookContext], None]:
    """Run the kernel's finish hooks the way marimo's runner does after a pass."""

    assert len(hooks.on_finish_hooks) == 1

    def finish(context: OnFinishHookContext) -> None:
        for hook in hooks.copy().on_finish_hooks:
            hook(context)

    return finish


def _notebook(tmp_path: Path) -> Path:
    source = tmp_path / "notebook.py"
    source.write_text(_NOTEBOOK, encoding="utf-8")
    return source


def _ledger(source: Path, repository: _Repository) -> ObservationLedger:
    return ObservationLedger(
        source,
        _repository_factory=lambda: ledger_module._OpenedObservations(
            repository=cast(Any, repository),
            close=repository.close,
        ),
    )


class _Kernel(SimpleNamespace):
    pass


_SESSION_SCOPE = "5c0ee8ec-d28d-4cb7-a4dc-4a77a54326a7"


def _kernel(source: Path, hooks: NotebookCellHooks) -> SimpleNamespace:
    """Return a kernel whose graph holds the saved cells as a live session does.

    A session can scope cell IDs with an external UUID and keep trailing
    whitespace that the saved file drops.
    """

    graph = DirectedGraph()
    for cell in composition.saved_cells(source, source.read_bytes()):
        cell_id = CellId_t(f"{_SESSION_SCOPE}{cell.id}")
        compiled = compile_cell(f"{cell.code}\n\n", cell_id=cell_id)
        compiled.configure(dict(cell.config))
        graph.register_cell(cell_id, compiled)
    return _Kernel(
        _hooks=hooks,
        graph=graph,
        app_metadata=SimpleNamespace(filename=str(source)),
    )


def _context(kernel: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(_kernel=kernel, filename=kernel.app_metadata.filename)


def _finished(
    kernel: SimpleNamespace,
    *,
    interrupted: bool = False,
    exceptions: Mapping[str, Exception] | None = None,
    cancelled: Mapping[str, set[str]] | None = None,
    scratch: bool = False,
) -> OnFinishHookContext:
    graph = kernel.graph
    if scratch:
        graph = DirectedGraph()
        graph.register_cell(SCRATCH_CELL_ID, compile_cell("probe = 1", cell_id=SCRATCH_CELL_ID))
    cancelled_cells = CancelledCells()
    for raising_cell, descendants in (cancelled or {}).items():
        cancelled_cells.add(CellId_t(raising_cell), {CellId_t(cell) for cell in descendants})
    return OnFinishHookContext(
        graph=graph,
        cells_to_run=tuple(graph.cells),
        interrupted=interrupted,
        cancelled_cells=cancelled_cells,
        exceptions={CellId_t(cell): error for cell, error in (exceptions or {}).items()},
    )


@pytest.fixture(autouse=True)
def _observed_scale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        inspection,
        "observe_kernel_inputs",
        lambda _kernel: KernelInputObservation({"scale": 3}, {}),
    )


def test_successful_normal_run_records_inputs_under_the_saved_producer(
    tmp_path: Path,
) -> None:
    source = _notebook(tmp_path)
    repository = _Repository()
    ledger = _ledger(source, repository)
    hooks = NotebookCellHooks()
    kernel = _kernel(source, hooks)

    release = install_observation_ledger(_context(kernel), ledger)
    _callback(hooks)(_finished(kernel))
    release()
    ledger.close()

    assert repository.records == [(producer_sha256(source), {"scale": 3}, 1)]


@pytest.mark.parametrize(
    "run",
    (
        {"interrupted": True},
        {"exceptions": {"cell": ValueError("failed")}},
        {"exceptions": {"cell": marimo.MarimoStopError(None)}},
        {"cancelled": {"cell": {"descendant"}}},
        {"scratch": True},
    ),
    ids=("interrupted", "exception", "stop", "cancelled", "scratch"),
)
def test_non_normal_runs_do_not_record(tmp_path: Path, run: dict[str, Any]) -> None:
    source = _notebook(tmp_path)
    repository = _Repository()
    ledger = _ledger(source, repository)
    hooks = NotebookCellHooks()
    kernel = _kernel(source, hooks)

    release = install_observation_ledger(_context(kernel), ledger)
    _callback(hooks)(_finished(kernel, **run))
    release()
    ledger.close()

    assert repository.records == []


@pytest.mark.parametrize(
    "edit",
    (
        lambda saved: saved.replace("value = 1", "value = 2"),
        lambda saved: saved.replace("@app.cell(hide_code=True)", "@app.cell"),
        lambda saved: saved.replace("@app.cell(column=1)", "@app.cell(column=2)"),
        lambda saved: saved.replace("@app.cell(column=1)", "@app.cell(column=1, disabled=True)"),
    ),
    ids=("code", "hide_code", "column", "disabled"),
)
def test_saved_source_edit_stops_matching_the_live_cells(
    tmp_path: Path,
    edit: Callable[[str], str],
) -> None:
    source = _notebook(tmp_path)
    repository = _Repository()
    ledger = _ledger(source, repository)
    hooks = NotebookCellHooks()
    kernel = _kernel(source, hooks)
    release = install_observation_ledger(_context(kernel), ledger)
    source.write_text(edit(_NOTEBOOK), encoding="utf-8")

    _callback(hooks)(_finished(kernel))
    release()
    ledger.close()

    assert repository.records == []


def test_release_stops_recording(tmp_path: Path) -> None:
    source = _notebook(tmp_path)
    repository = _Repository()
    ledger = _ledger(source, repository)
    hooks = NotebookCellHooks()
    kernel = _kernel(source, hooks)

    release = install_observation_ledger(_context(kernel), ledger)
    _callback(hooks)(_finished(kernel))
    ledger.flush()
    release()
    release()
    _callback(hooks)(_finished(kernel))
    ledger.close()

    assert len(repository.records) == 1


def test_install_rejects_a_kernel_bound_to_another_source(tmp_path: Path) -> None:
    source = _notebook(tmp_path)
    other = tmp_path / "other.py"
    other.write_text(_NOTEBOOK, encoding="utf-8")
    ledger = _ledger(source, _Repository())
    hooks = NotebookCellHooks()

    with pytest.raises(ValueError, match="source"):
        install_observation_ledger(_context(_kernel(other, hooks)), ledger)
    ledger.close()

    assert hooks.on_finish_hooks == []


def test_install_rejects_a_context_without_a_kernel(tmp_path: Path) -> None:
    ledger = _ledger(_notebook(tmp_path), _Repository())

    with pytest.raises(TypeError, match="kernel"):
        install_observation_ledger(SimpleNamespace(filename=str(ledger.source)), ledger)
    ledger.close()


def test_live_source_rename_stops_the_existing_registration(tmp_path: Path) -> None:
    source = _notebook(tmp_path)
    repository = _Repository()
    ledger = _ledger(source, repository)
    hooks = NotebookCellHooks()
    kernel = _kernel(source, hooks)
    release = install_observation_ledger(_context(kernel), ledger)
    renamed = tmp_path / "renamed.py"
    renamed.write_text(_NOTEBOOK, encoding="utf-8")
    kernel.app_metadata.filename = str(renamed)

    _callback(hooks)(_finished(kernel))
    release()
    ledger.close()

    assert repository.records == []


def test_unreadable_kernel_graph_logs_and_leaves_the_run_intact(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = _notebook(tmp_path)
    repository = _Repository()
    ledger = _ledger(source, repository)
    hooks = NotebookCellHooks()
    kernel = _kernel(source, hooks)
    release = install_observation_ledger(_context(kernel), ledger)
    kernel.graph.cells["broken"] = SimpleNamespace(code="x = 1", config=object())

    with caplog.at_level(logging.WARNING):
        _callback(hooks)(_finished(kernel))
    release()
    ledger.close()

    assert repository.records == []
    assert "Could not record notebook input state" in caplog.text


def test_finish_hook_defers_saved_parse_and_producer_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _notebook(tmp_path)
    repository = _Repository()
    ledger = _ledger(source, repository)
    hooks = NotebookCellHooks()
    kernel = _kernel(source, hooks)
    read_started = threading.Event()
    read_release = threading.Event()
    native_read = source_module._read_saved_producer

    def read_saved(path: Path, expected: Any) -> Any:
        read_started.set()
        assert read_release.wait(5)
        return native_read(path, expected)

    monkeypatch.setattr(source_module, "_read_saved_producer", read_saved)
    release = install_observation_ledger(_context(kernel), ledger)
    callback_finished = threading.Event()
    callback_errors: list[BaseException] = []

    def finish_cell() -> None:
        try:
            _callback(hooks)(_finished(kernel))
        except BaseException as error:
            callback_errors.append(error)
        finally:
            callback_finished.set()

    caller = threading.Thread(target=finish_cell)
    caller.start()
    try:
        assert read_started.wait(5)
        assert callback_finished.wait(5)
    finally:
        read_release.set()
        caller.join(timeout=5)
    release()
    ledger.close()

    assert not caller.is_alive()
    assert callback_errors == []
    assert repository.records == [(producer_sha256(source), {"scale": 3}, 1)]


def test_repeated_installs_share_one_kernel_hook(tmp_path: Path) -> None:
    source = _notebook(tmp_path)
    repositories = (_Repository(), _Repository())
    ledgers = tuple(_ledger(source, repository) for repository in repositories)
    hooks = NotebookCellHooks()
    kernel = _kernel(source, hooks)
    context = _context(kernel)

    releases = tuple(install_observation_ledger(context, ledger) for ledger in ledgers)
    callback = _callback(hooks)
    callback(_finished(kernel))
    ledgers[0].flush()
    ledgers[1].flush()
    releases[0]()
    callback(_finished(kernel))
    releases[1]()
    for ledger in ledgers:
        ledger.close()

    assert len(repositories[0].records) == 1
    assert len(repositories[1].records) == 2


def test_released_subscription_leaves_the_kernel_collectable(tmp_path: Path) -> None:
    source = _notebook(tmp_path)
    ledger = _ledger(source, _Repository())
    hooks = NotebookCellHooks()
    kernel = _kernel(source, hooks)
    install_observation_ledger(_context(kernel), ledger)()
    ledger.close()
    collected = weakref.ref(kernel)

    del kernel
    gc.collect()

    assert hooks.on_finish_hooks
    assert collected() is None


def test_unreleased_subscription_leaves_the_kernel_collectable(tmp_path: Path) -> None:
    source = _notebook(tmp_path)
    ledger = _ledger(source, _Repository())
    kernel = _kernel(source, NotebookCellHooks())
    install_observation_ledger(_context(kernel), ledger)
    ledger.close()
    collected = weakref.ref(kernel)

    del kernel
    gc.collect()

    assert collected() is None


def test_raising_subscriber_leaves_other_subscribers_and_the_run_intact(tmp_path: Path) -> None:
    hooks = NotebookCellHooks()
    kernel = _kernel(_notebook(tmp_path), hooks)
    received: list[KernelRun] = []

    def fail(_run: KernelRun) -> None:
        raise RuntimeError("subscriber failed")

    releases = (
        composition.subscribe_run_finished(_context(kernel), fail),
        composition.subscribe_run_finished(_context(kernel), received.append),
    )
    _callback(hooks)(_finished(kernel))
    for release in releases:
        release()

    assert [run.outcome for run in received] == ["completed"]


def test_saved_producer_requires_one_stable_source_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _notebook(tmp_path)
    revision = source_module._source_revision(source)

    def identify(path: Path) -> str:
        path.write_text(_NOTEBOOK + "\n# changed\n", encoding="utf-8")
        return "a" * 64

    monkeypatch.setattr(source_module, "identify_producer", identify)

    assert source_module._read_saved_producer(source, revision) is None
