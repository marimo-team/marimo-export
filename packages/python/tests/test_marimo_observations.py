from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import marimo_export._marimo.compat.observations as compat
import marimo_export._marimo.composition as composition
import marimo_export._marimo.entrypoints as entrypoints
import marimo_export._observations.ledger as ledger_module
import pytest
from marimo._runtime.scratch import SCRATCH_CELL_ID
from marimo_export.observations import (
    ObservationLedger,
    ObservedInputs,
    install_observation_ledger,
)


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


class _Hooks:
    def __init__(self) -> None:
        self.callbacks: list[Callable[[object], None]] = []
        self.priorities: list[object] = []

    def add_on_finish(self, callback: Callable[[object], None], priority: object) -> None:
        self.callbacks.append(callback)
        self.priorities.append(priority)


def _callback(hooks: _Hooks) -> Callable[[object], None]:
    assert len(hooks.callbacks) == 1
    return hooks.callbacks[0]


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "notebook.py"
    source.write_text("one", encoding="utf-8")
    return source


def _installed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ObservationLedger, _Repository, _Hooks, object, Any, Any]:
    repository = _Repository()
    ledger = ObservationLedger(
        _source(tmp_path),
        _repository_factory=lambda: ledger_module._OpenedObservations(
            repository=cast(Any, repository),
            close=repository.close,
        ),
    )
    hooks = _Hooks()
    graph = SimpleNamespace(cells=OrderedDict())
    kernel = SimpleNamespace(
        _hooks=hooks,
        graph=graph,
        app_metadata=SimpleNamespace(filename=str(ledger.source)),
    )
    context = SimpleNamespace(_kernel=kernel, filename=str(ledger.source))
    monkeypatch.setattr(
        compat._DocumentMatcher,
        "defer",
        lambda _self, _signature: ("scope", lambda: "a" * 64),
    )
    monkeypatch.setattr(
        compat,
        "_observed_inputs",
        lambda _kernel: ObservedInputs({"scale": 3}),
    )
    release = compat.install_observation_ledger(context, ledger)
    assert hooks.callbacks
    return ledger, repository, hooks, graph, context, release


def _finished(
    graph: object,
    *,
    interrupted: bool = False,
    exceptions: tuple[object, ...] = (),
    cancelled_cells: tuple[object, ...] = (),
) -> object:
    return SimpleNamespace(
        interrupted=interrupted,
        exceptions=exceptions,
        cancelled_cells=cancelled_cells,
        graph=graph,
    )


def test_successful_normal_run_records_complete_observed_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, repository, hooks, graph, _context, release = _installed(tmp_path, monkeypatch)

    _callback(hooks)(_finished(graph))
    release()
    ledger.close()

    assert repository.records == [("a" * 64, {"scale": 3}, 1)]


@pytest.mark.parametrize(
    ("interrupted", "exceptions", "cancelled_cells", "scratch"),
    (
        (True, (), (), False),
        (False, (ValueError("failed"),), (), False),
        (False, (), ("cell-one",), False),
        (False, (), (), True),
    ),
)
def test_non_normal_runs_do_not_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interrupted: bool,
    exceptions: tuple[object, ...],
    cancelled_cells: tuple[object, ...],
    scratch: bool,
) -> None:
    ledger, repository, hooks, graph, _context, release = _installed(tmp_path, monkeypatch)
    run_graph = graph
    if scratch:
        run_graph = SimpleNamespace(cells={SCRATCH_CELL_ID: object()})

    _callback(hooks)(
        _finished(
            run_graph,
            interrupted=interrupted,
            exceptions=exceptions,
            cancelled_cells=cancelled_cells,
        )
    )
    release()
    ledger.close()

    assert repository.records == []


def test_release_disables_the_installed_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, repository, hooks, graph, _context, release = _installed(tmp_path, monkeypatch)

    _callback(hooks)(_finished(graph))
    ledger.flush()
    release()
    release()
    _callback(hooks)(_finished(graph))
    ledger.close()

    assert repository.records == [("a" * 64, {"scale": 3}, 1)]


def test_install_rejects_a_kernel_bound_to_another_source(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    other = tmp_path / "other.py"
    other.write_text("other", encoding="utf-8")
    ledger = ObservationLedger(
        source,
        _repository_factory=lambda: ledger_module._OpenedObservations(
            repository=cast(Any, _Repository()),
            close=lambda: None,
        ),
    )
    hooks = _Hooks()
    context = SimpleNamespace(
        _kernel=SimpleNamespace(_hooks=hooks, graph=SimpleNamespace(cells={})),
        filename=str(other),
    )
    context._kernel.app_metadata = SimpleNamespace(filename=str(other))

    with pytest.raises(ValueError, match="source"):
        compat.install_observation_ledger(context, ledger)
    ledger.close()

    assert hooks.callbacks == []


def test_live_source_rename_stops_the_existing_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, repository, hooks, graph, context, release = _installed(tmp_path, monkeypatch)
    callback = _callback(hooks)
    renamed = tmp_path / "renamed.py"
    renamed.write_text("renamed", encoding="utf-8")
    context._kernel.app_metadata.filename = str(renamed)

    callback(_finished(graph))
    release()
    ledger.close()

    assert repository.records == []


def test_finish_hook_defers_saved_parse_and_producer_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    repository = _Repository()
    ledger = ObservationLedger(
        source,
        _repository_factory=lambda: ledger_module._OpenedObservations(
            repository=cast(Any, repository),
            close=repository.close,
        ),
    )
    hooks = _Hooks()
    graph = SimpleNamespace(cells=OrderedDict())
    kernel = SimpleNamespace(
        _hooks=hooks,
        graph=graph,
        app_metadata=SimpleNamespace(filename=str(source)),
    )
    context = SimpleNamespace(_kernel=kernel, filename=str(source))
    revision = compat._source_revision(source)
    read_started = threading.Event()
    read_release = threading.Event()

    def read_saved(_source: Path, expected: object) -> compat._SavedProducer:
        assert expected == revision
        read_started.set()
        assert read_release.wait(5)
        return compat._SavedProducer(revision, "a" * 64, compat._live_cell_signature(kernel))

    monkeypatch.setattr(compat, "_read_saved_producer", read_saved)
    monkeypatch.setattr(
        compat,
        "_observed_inputs",
        lambda _kernel: ObservedInputs({"scale": 3}),
    )
    release = compat.install_observation_ledger(context, ledger)
    callback_finished = threading.Event()
    callback_errors: list[BaseException] = []

    def finish_cell() -> None:
        try:
            _callback(hooks)(_finished(graph))
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
    assert repository.records == [("a" * 64, {"scale": 3}, 1)]


def test_repeated_installs_share_one_hook_dispatcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    repositories = (_Repository(), _Repository())
    ledgers = tuple(
        ObservationLedger(
            source,
            _repository_factory=lambda current=current: ledger_module._OpenedObservations(
                repository=cast(Any, current),
                close=current.close,
            ),
        )
        for current in repositories
    )
    hooks = _Hooks()
    graph = SimpleNamespace(cells=OrderedDict())
    kernel = SimpleNamespace(
        _hooks=hooks,
        graph=graph,
        app_metadata=SimpleNamespace(filename=str(source)),
    )
    context = SimpleNamespace(_kernel=kernel, filename=str(source))
    monkeypatch.setattr(
        compat._DocumentMatcher,
        "defer",
        lambda _self, _signature: ("scope", lambda: "a" * 64),
    )
    monkeypatch.setattr(
        compat,
        "_observed_inputs",
        lambda _kernel: ObservedInputs({"scale": 3}),
    )

    releases = tuple(compat.install_observation_ledger(context, ledger) for ledger in ledgers)
    assert len(hooks.callbacks) == 1
    callback = _callback(hooks)
    callback(_finished(graph))
    ledgers[0].flush()
    ledgers[1].flush()
    releases[0]()
    callback(_finished(graph))
    releases[1]()
    for ledger in ledgers:
        ledger.close()

    assert len(repositories[0].records) == 1
    assert len(repositories[1].records) == 2


def test_public_installer_delegates_through_the_entrypoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = ObservationLedger(
        _source(tmp_path),
        _repository_factory=lambda: ledger_module._OpenedObservations(
            repository=cast(Any, _Repository()),
            close=lambda: None,
        ),
    )
    calls: list[tuple[object, ObservationLedger]] = []

    def release() -> None:
        pass

    monkeypatch.setattr(
        entrypoints,
        "install_observation_ledger",
        lambda context, current: calls.append((context, current)) or release,
    )
    context = object()

    installed = install_observation_ledger(context, ledger)
    ledger.close()

    assert calls == [(context, ledger)]
    assert installed is release


def test_entrypoint_delegates_through_composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = ObservationLedger(
        _source(tmp_path),
        _repository_factory=lambda: ledger_module._OpenedObservations(
            repository=cast(Any, _Repository()),
            close=lambda: None,
        ),
    )
    calls: list[tuple[object, ObservationLedger]] = []

    def release() -> None:
        pass

    monkeypatch.setattr(
        composition,
        "install_observation_ledger",
        lambda context, current: calls.append((context, current)) or release,
    )
    context = object()

    installed = entrypoints.install_observation_ledger(context, ledger)
    ledger.close()

    assert calls == [(context, ledger)]
    assert installed is release


def test_saved_and_live_cell_signatures_match_code_order_id_and_config(
    tmp_path: Path,
) -> None:
    source = tmp_path / "notebook.py"
    source.write_text(
        """
import marimo

app = marimo.App()


@app.cell(hide_code=True)
def first():
    value = 1
    return (value,)


@app.cell(column=1)
def second(value):
    doubled = value * 2
    return (doubled,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    saved = compat._saved_cell_signature(source, source.read_bytes())
    records = (
        ("cell-first", "value = 1", None, False, True),
        ("cell-second", "doubled = value * 2", 1, False, False),
    )
    expected_live = compat._cell_signature(records)
    cells: OrderedDict[str, object] = OrderedDict()
    for cell_id, code, column, disabled, hide_code in records:
        cells[cell_id] = SimpleNamespace(
            code=code,
            config=SimpleNamespace(
                column=column,
                disabled=disabled,
                hide_code=hide_code,
            ),
        )
    kernel = SimpleNamespace(graph=SimpleNamespace(cells=cells))

    assert len(saved) == 64
    assert compat._live_cell_signature(kernel) == expected_live
    first = cast(SimpleNamespace, next(iter(cells.values())))
    first.code += "\nchanged = True"
    assert compat._live_cell_signature(kernel) != expected_live


def test_saved_producer_requires_one_stable_source_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "notebook.py"
    source.write_text("one", encoding="utf-8")
    revision = compat._source_revision(source)
    monkeypatch.setattr(compat, "_saved_cell_signature", lambda _path, _payload: ())

    def identify(path: Path) -> str:
        path.write_text("changed source", encoding="utf-8")
        return "a" * 64

    monkeypatch.setattr(compat, "identify_producer", identify)

    assert compat._read_saved_producer(source, revision) is None
