from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from types import SimpleNamespace, TracebackType
from typing import Any

import pytest
from marimo._runtime.control_flow import MarimoStopError
from marimo_export._marimo import code_mode
from marimo_export.errors import SelectionError
from marimo_export.spec import Source


class FakeConfig:
    def __init__(self, **values: object) -> None:
        self._values = values

    def asdict(self) -> dict[str, object]:
        return dict(self._values)


@dataclass
class FakeOutput:
    mimetype: str
    data: object


@dataclass
class FakeCell:
    id: str
    code: str
    name: str = ""
    config: FakeConfig = field(
        default_factory=lambda: FakeConfig(
            disabled=False,
            hide_code=False,
            column=None,
        )
    )
    status: str | None = "idle"
    output: FakeOutput | None = None


class FakeCells:
    def __init__(self, cells: list[FakeCell]) -> None:
        self._cells = cells

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._cells)

    def __getitem__(self, key: int | str) -> FakeCell:
        if isinstance(key, int):
            return self._cells[key]
        for cell in self._cells:
            if cell.id == key or cell.name == key:
                return cell
        raise KeyError(key)


class FakeControl:
    def __init__(
        self,
        value: object,
        component_args: dict[str, object] | None = None,
        *,
        element: FakeControl | None = None,
        elements: dict[str, FakeControl] | list[FakeControl] | None = None,
    ) -> None:
        self._value_frontend = value
        self._component_args = dict(component_args or {})
        self.element = element
        self.elements = elements


@dataclass
class FakeRunCell:
    cell_id: str


@dataclass
class FakeRunResult:
    output: object
    exception: object | None = None
    accumulated_output: object | None = None


@dataclass
class FakeAccumulatedOutput:
    value: object
    stack_calls: int = 0

    def stack(self) -> object:
        self.stack_calls += 1
        return self.value


class FakeHooks:
    def __init__(self, post: list[Any] | None = None) -> None:
        self.post = list(post or [])

    def copy(self) -> FakeHooks:
        return FakeHooks(self.post)

    def add_post_execution(self, hook: Any, priority: object) -> None:
        del priority
        self.post.append(hook)


class FakeGraph:
    def __init__(
        self,
        cells: list[FakeCell],
        definitions: dict[str, set[str]] | None = None,
    ) -> None:
        self.cells = {
            cell.id: SimpleNamespace(stale=False, run_result_status="idle") for cell in cells
        }
        self.definitions = definitions or {}

    def get_defining_cells(self, name: str) -> set[str]:
        return set(self.definitions.get(name, set()))

    def get_stale(self) -> set[str]:
        return {cell_id for cell_id, cell in self.cells.items() if cell.stale}

    def set_stale(self, cell_ids: set[str]) -> None:
        for cell_id in cell_ids:
            self.cells[cell_id].stale = True


class FakeContext:
    def __init__(
        self,
        *,
        globals: dict[str, object],
        cells: list[FakeCell],
        filename: str = "/srv/notebooks/finance.py",
        reactive_mode: str = "autorun",
        definitions: dict[str, set[str]] | None = None,
    ) -> None:
        self.globals = globals
        self.cells = FakeCells(cells)
        self._kernel = SimpleNamespace(
            app_metadata=SimpleNamespace(filename=filename),
            _hooks=FakeHooks(),
            reactive_execution_mode=reactive_mode,
            graph=FakeGraph(cells, definitions),
        )
        self.batches: list[list[tuple[FakeControl, object]]] = []
        self.runs: list[tuple[FakeRunCell, FakeRunResult]] = []
        self._queued: list[tuple[FakeControl, object]] = []
        self._run_targets: set[str] = set()

    async def __aenter__(self) -> FakeContext:
        self._queued = []
        self._run_targets = set()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        del exc_val, exc_tb
        if exc_type is not None:
            return
        batch = list(self._queued)
        self.batches.append(batch)
        for control, value in batch:
            control._value_frontend = value
        if batch and self._kernel.reactive_execution_mode == "lazy":
            for cell in self._kernel.graph.cells.values():
                cell.stale = True
        should_run = self._kernel.reactive_execution_mode == "autorun"
        for cell, result in self.runs:
            if not should_run and cell.cell_id not in self._run_targets:
                continue
            for hook in self._kernel._hooks.post:
                hook(cell, object(), result)
            graph_cell = self._kernel.graph.cells[cell.cell_id]
            graph_cell.stale = False
            graph_cell.run_result_status = "exception" if result.exception is not None else "idle"

    def set_ui_value(self, element: Any, value: Any) -> None:
        self._queued.append((element, value))

    def run_cell(self, target: str) -> None:
        self._run_targets.add(target)


def _use_context(monkeypatch: pytest.MonkeyPatch, ctx: FakeContext) -> None:
    monkeypatch.setattr(code_mode, "get_context", lambda: ctx)
    monkeypatch.setattr(code_mode, "UIElement", FakeControl)
    monkeypatch.setattr(
        code_mode,
        "_rendered_output",
        lambda value: FakeOutput("application/json", value),
    )


def test_inspect_live_reports_ordered_document_and_direct_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = FakeControl(("AAPL",))
    output = FakeOutput("text/html", "<p>ready</p>")
    cells = [
        FakeCell(
            id="cell-b",
            code="chart",
            name="chart_cell",
            config=FakeConfig(disabled=False, hide_code=True, column=2),
            output=output,
        ),
        FakeCell(
            id="cell-a",
            code="value = 1",
            config=FakeConfig(disabled=False, hide_code=False, column=None),
            status="stale",
        ),
    ]
    ctx = FakeContext(
        globals={
            "z_value": 3,
            "symbol_picker": control,
            "_cell_abcd_local": "hidden",
            "__builtins__": {},
        },
        cells=cells,
    )
    _use_context(monkeypatch, ctx)

    wire = asyncio.run(code_mode.inspect_live()).wire()

    document = [
        {
            "id": "cell-b",
            "code": "chart",
            "name": "chart_cell",
            "config": {"disabled": False, "hide_code": True, "column": 2},
        },
        {
            "id": "cell-a",
            "code": "value = 1",
            "name": "",
            "config": {"disabled": False, "hide_code": False, "column": None},
        },
    ]
    encoded = json.dumps(
        document,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    assert wire == {
        "notebook": {
            "filename": "finance.py",
            "path": "/srv/notebooks/finance.py",
            "document_sha256": hashlib.sha256(encoded).hexdigest(),
        },
        "globals": [
            {
                "name": "symbol_picker",
                "python_type": f"{FakeControl.__module__}.FakeControl",
            },
            {"name": "z_value", "python_type": "builtins.int"},
        ],
        "cells": [
            {
                "id": "cell-b",
                "name": "chart_cell",
                "status": "idle",
                "has_output": True,
                "media_type": "text/html",
            },
            {
                "id": "cell-a",
                "name": None,
                "status": "stale",
                "has_output": False,
                "media_type": None,
            },
        ],
        "controls": [
            {
                "name": "symbol_picker",
                "type": "FakeControl",
                "value": ["AAPL"],
                "sensitive": False,
                "domain": {},
            }
        ],
    }

    original_digest = code_mode.document_sha256(ctx)
    ctx.cells = FakeCells(list(reversed(cells)))
    assert code_mode.document_sha256(ctx) != original_digest


def test_inspect_live_redacts_passwords_and_filters_control_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapped_password = FakeControl(
        "wrapped-private-password",
        element=FakeControl(
            "wrapped-private-password",
            {"kind": "password", "placeholder": "wrapped-private-password"},
        ),
    )
    composite_password = FakeControl(
        {"token": "composite-private-password"},
        elements={
            "token": FakeControl(
                "composite-private-password",
                {"kind": "password"},
            )
        },
    )
    wrapped_dropdown = FakeControl(
        ["AAPL"],
        element=FakeControl(
            ["AAPL"],
            {"options": ["AAPL", "MSFT"], "max-selections": 1},
        ),
    )
    composite_filters = FakeControl(
        {"symbol": ["AAPL"], "window": 30},
        elements={
            "symbol": FakeControl(["AAPL"], {"options": ["AAPL", "MSFT"]}),
            "window": FakeControl(30, {"start": 1, "stop": 90, "step": 1}),
        },
    )
    ctx = FakeContext(
        globals={
            "credentials": composite_password,
            "filters": composite_filters,
            "password": FakeControl(
                "private-password",
                {
                    "kind": "password",
                    "placeholder": "private-password",
                    "options": ["private-password"],
                },
            ),
            "symbol": FakeControl(
                ["AAPL"],
                {
                    "options": ["AAPL", "MSFT"],
                    "max-selections": 1,
                    "placeholder": "choose a symbol",
                    "precision": object(),
                },
            ),
            "password_form": wrapped_password,
            "symbol_form": wrapped_dropdown,
        },
        cells=[],
    )
    _use_context(monkeypatch, ctx)

    controls = asyncio.run(code_mode.inspect_live()).wire()["controls"]

    assert controls == [
        {
            "name": "credentials",
            "type": "FakeControl",
            "value": None,
            "sensitive": True,
            "domain": {},
        },
        {
            "name": "filters",
            "type": "FakeControl",
            "value": {"symbol": ["AAPL"], "window": 30},
            "sensitive": False,
            "domain": {
                "symbol": {"options": ["AAPL", "MSFT"]},
                "window": {"start": 1, "step": 1, "stop": 90},
            },
        },
        {
            "name": "password",
            "type": "FakeControl",
            "value": None,
            "sensitive": True,
            "domain": {},
        },
        {
            "name": "password_form",
            "type": "FakeControl",
            "value": None,
            "sensitive": True,
            "domain": {},
        },
        {
            "name": "symbol",
            "type": "FakeControl",
            "value": ["AAPL"],
            "sensitive": False,
            "domain": {
                "max-selections": 1,
                "options": ["AAPL", "MSFT"],
            },
        },
        {
            "name": "symbol_form",
            "type": "FakeControl",
            "value": ["AAPL"],
            "sensitive": False,
            "domain": {
                "max-selections": 1,
                "options": ["AAPL", "MSFT"],
            },
        },
    ]
    assert "private-password" not in json.dumps(controls)
    assert "wrapped-private-password" not in json.dumps(controls)
    assert "composite-private-password" not in json.dumps(controls)
    assert "placeholder" not in json.dumps(controls)


def test_python_type_is_side_effect_free_sanitized_and_bounded() -> None:
    class ExplosiveMeta(type):
        def __getattribute__(self, name: str) -> object:
            if name in {"__module__", "__qualname__"}:
                raise AssertionError("metaclass attribute lookup must not run")
            return super().__getattribute__(name)

    class SafeType(metaclass=ExplosiveMeta):
        pass

    assert code_mode._python_type(SafeType()) == (
        f"{__name__}.test_python_type_is_side_effect_free_sanitized_and_bounded.<locals>.SafeType"
    )

    module = f"pkg\n{'x' * 600}\ud800"
    dynamic_type = type("Dynamic", (), {"__module__": module})
    descriptor = code_mode._python_type(dynamic_type())
    escaped = f"pkg\\u000a{'x' * 600}\\ud800.Dynamic"
    digest = hashlib.sha256(escaped.encode()).hexdigest()

    assert len(descriptor.encode()) <= 512
    assert descriptor.endswith(f"#sha256:{digest}")
    assert "\n" not in descriptor
    assert "\ud800" not in descriptor


def test_resolve_source_uses_globals_expressions_and_fresh_cell_outputs() -> None:
    frozen = FakeOutput("text/html", "<p>old</p>")
    ctx = FakeContext(
        globals={"price": 21},
        cells=[FakeCell("cell-a", "price * 2", "report", output=frozen)],
    )

    assert code_mode.resolve_source(Source("global", "price"), ctx) == 21
    assert code_mode.resolve_source(Source("expression", "price * 2"), ctx) == 42
    assert code_mode.resolve_source(Source("cell", "report"), ctx) is frozen
    assert (
        code_mode.resolve_source(Source("cell", "report"), ctx, {"cell-a": "fresh raw value"})
        == "fresh raw value"
    )

    with pytest.raises(SelectionError, match="unavailable"):
        code_mode.resolve_source(Source("global", "missing"), ctx)
    with pytest.raises(SelectionError, match="ZeroDivisionError"):
        code_mode.resolve_source(Source("expression", "1 / 0"), ctx)


def test_snapshot_and_apply_controls_use_one_batch_and_capture_fresh_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    symbol = FakeControl(["MSFT"])
    horizon = FakeControl(30)
    ctx = FakeContext(
        globals={"symbol": symbol, "horizon": horizon},
        cells=[
            FakeCell("cell-chart", "chart", "chart"),
            FakeCell("cell-table", "table"),
        ],
    )
    ctx.runs = [
        (FakeRunCell("cell-chart"), FakeRunResult({"mark": "line"})),
        (FakeRunCell("cell-table"), FakeRunResult([1, 2, 3])),
    ]
    _use_context(monkeypatch, ctx)

    snapshot = asyncio.run(code_mode.snapshot_controls(["symbol", "horizon"]))
    original_hooks = ctx._kernel._hooks
    applied = asyncio.run(code_mode.apply_controls({"symbol": ["AAPL"], "horizon": 90}))

    assert dict(snapshot.values) == {"symbol": ["MSFT"], "horizon": 30}
    assert len(ctx.batches[-1]) == 2
    assert symbol._value_frontend == ["AAPL"]
    assert horizon._value_frontend == 90
    assert applied.outputs["cell-chart"] == FakeOutput(
        "application/json",
        {"mark": "line"},
    )
    assert applied.outputs["chart"] == FakeOutput(
        "application/json",
        {"mark": "line"},
    )
    assert applied.outputs["cell-table"] == FakeOutput(
        "application/json",
        [1, 2, 3],
    )
    assert ctx._kernel._hooks is original_hooks
    assert original_hooks.post == []

    asyncio.run(code_mode.restore_controls(snapshot))
    assert len(ctx.batches[-1]) == 2
    assert symbol._value_frontend == ["MSFT"]
    assert horizon._value_frontend == 30


def test_apply_controls_captures_accumulated_output_on_variant_reruns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    symbol = FakeControl(["MSFT"])
    ctx = FakeContext(
        globals={"symbol": symbol},
        cells=[FakeCell("cell-report", "report", "report")],
    )
    _use_context(monkeypatch, ctx)

    ctx.runs = [
        (
            FakeRunCell("cell-report"),
            FakeRunResult(
                None,
                accumulated_output=FakeAccumulatedOutput(["first", "second"]),
            ),
        )
    ]
    appended = asyncio.run(code_mode.apply_controls({"symbol": ["AAPL"]}))

    ctx.runs = [
        (
            FakeRunCell("cell-report"),
            FakeRunResult(
                None,
                accumulated_output=FakeAccumulatedOutput("replacement"),
            ),
        )
    ]
    replaced = asyncio.run(code_mode.apply_controls({"symbol": ["GOOG"]}))

    assert appended.outputs["report"] == FakeOutput(
        "application/json",
        ["first", "second"],
    )
    assert replaced.outputs["report"] == FakeOutput(
        "application/json",
        "replacement",
    )


def test_apply_controls_prefers_terminal_output_over_accumulated_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    symbol = FakeControl(["MSFT"])
    accumulated = FakeAccumulatedOutput("imperative")
    ctx = FakeContext(
        globals={"symbol": symbol},
        cells=[FakeCell("cell-report", "report", "report")],
    )
    ctx.runs = [
        (
            FakeRunCell("cell-report"),
            FakeRunResult("terminal", accumulated_output=accumulated),
        )
    ]
    _use_context(monkeypatch, ctx)

    applied = asyncio.run(code_mode.apply_controls({"symbol": ["AAPL"]}))

    assert applied.outputs["report"] == FakeOutput(
        "application/json",
        "terminal",
    )
    assert accumulated.stack_calls == 0


def test_apply_controls_reports_reactive_failure_and_restores_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    symbol = FakeControl(["MSFT"])
    ctx = FakeContext(
        globals={"symbol": symbol},
        cells=[FakeCell("cell-chart", "chart", "chart")],
    )
    ctx.runs = [
        (
            FakeRunCell("cell-chart"),
            FakeRunResult(None, ValueError("bad symbol")),
        )
    ]
    _use_context(monkeypatch, ctx)
    original_hooks = ctx._kernel._hooks

    with pytest.raises(SelectionError, match=r"chart.*ValueError: bad symbol"):
        asyncio.run(code_mode.apply_controls({"symbol": ["INVALID"]}))

    assert ctx._kernel._hooks is original_hooks
    assert original_hooks.post == []


def test_apply_controls_captures_marimo_stop_display_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    symbol = FakeControl(["MSFT"])
    stop = MarimoStopError("select a symbol")
    ctx = FakeContext(
        globals={"symbol": symbol},
        cells=[FakeCell("cell-report", "report", "report")],
    )
    ctx.runs = [
        (
            FakeRunCell("cell-report"),
            FakeRunResult(stop.output, stop),
        )
    ]
    _use_context(monkeypatch, ctx)

    applied = asyncio.run(code_mode.apply_controls({"symbol": ["AAPL"]}))

    assert applied.outputs["report"] == FakeOutput(
        "application/json",
        "select a symbol",
    )


def test_controls_require_direct_named_ui_globals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = FakeContext(globals={"plain": 1}, cells=[])
    _use_context(monkeypatch, ctx)

    with pytest.raises(SelectionError, match="direct UI control"):
        asyncio.run(code_mode.snapshot_controls(["plain"]))
    with pytest.raises(SelectionError, match="Python identifiers"):
        asyncio.run(code_mode.apply_controls({"not a name": 1}))


def test_lazy_controls_refresh_selected_sources_and_restore_staleness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    symbol = FakeControl(["MSFT"])
    cells = [
        FakeCell("cell-chart", "chart = build(symbol.value)", "chart_cell"),
        FakeCell("cell-note", "note = symbol.value", "note_cell"),
    ]
    ctx = FakeContext(
        globals={"symbol": symbol, "chart": {"mark": "old"}},
        cells=cells,
        reactive_mode="lazy",
        definitions={"chart": {"cell-chart"}},
    )
    ctx.runs = [
        (FakeRunCell("cell-chart"), FakeRunResult({"mark": "fresh"})),
        (FakeRunCell("cell-note"), FakeRunResult("fresh note")),
    ]
    _use_context(monkeypatch, ctx)
    state = code_mode.snapshot_cell_state()

    applied = asyncio.run(
        code_mode.apply_controls(
            {"symbol": ["AAPL"]},
            [Source("global", "chart")],
        )
    )

    assert applied.outputs["cell-chart"] == FakeOutput(
        "application/json",
        {"mark": "fresh"},
    )
    assert ctx._kernel.graph.cells["cell-chart"].stale is False
    assert ctx._kernel.graph.cells["cell-note"].stale is True

    symbol._value_frontend = ["MSFT"]
    asyncio.run(code_mode.restore_cell_state(state))

    assert ctx._kernel.graph.get_stale() == set()
    assert "cell-note" in ctx._run_targets
