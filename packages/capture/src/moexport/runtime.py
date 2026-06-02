"""Read-only notebook facade exposed as ``mox.runtime()``."""

from __future__ import annotations

import ast
import hashlib
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cached_property
from pathlib import Path
from typing import Any, Protocol

from marimo._ast.cell import CellImpl
from marimo._ast.load import get_notebook_status
from marimo._ast.names import DEFAULT_CELL_NAME, TOPLEVEL_CELL_PREFIX
from marimo._messaging.notebook.document import NotebookDocument, get_current_document
from marimo._runtime.context import get_context
from marimo._runtime.context.types import RuntimeContext
from marimo._types.ids import CellId_t

from moexport.snapshots import (
    CellSnapshot,
    NotebookSnapshot,
    OutputSnapshot,
    format_output,
    json_object,
)


class RuntimeBinding(Protocol):
    """Scenario-local values made available while ``mox.evaluate`` runs."""

    @property
    def runtime(self) -> RuntimeContext:
        """Marimo runtime associated with the active scenario."""
        raise NotImplementedError()

    def cell_output(self, cell_id: CellId_t) -> Any:
        """Return a cell output materialized for the active scenario."""
        raise NotImplementedError()


_ACTIVE_RUNTIME: ContextVar[RuntimeBinding | None] = ContextVar(
    "moexport_active_runtime",
    default=None,
)


@contextmanager
def bind_runtime(binding: RuntimeBinding) -> Generator[None]:
    """Make scenario-local evaluation values visible to ``mox.runtime()``."""

    token = _ACTIVE_RUNTIME.set(binding)
    try:
        yield
    finally:
        _ACTIVE_RUNTIME.reset(token)


def expression_globals() -> dict[str, Any]:
    """Globals intentionally available to user-authored export expressions."""

    import moexport as mox

    return {
        "mox": mox,
        "moexport": mox,
    }


def runtime() -> NotebookRuntime:
    """Return a read-only handle to the current marimo notebook."""

    active = _ACTIVE_RUNTIME.get()
    return NotebookRuntime(
        None if active is None else active.runtime,
        evaluation=active,
    )


class NotebookRuntime:
    """Structured access to the notebook currently being exported."""

    def __init__(
        self,
        runtime: RuntimeContext | None = None,
        *,
        evaluation: RuntimeBinding | None = None,
    ) -> None:
        self._runtime = runtime or get_context()
        self._evaluation = evaluation
        self.notebook = RuntimeNotebook(self._runtime)

    def cells(self) -> list[RuntimeCell]:
        """Return valid cells in notebook order when marimo exposes it."""

        return [
            RuntimeCell(notebook=self, cell_id=cell_id, index=index)
            for index, cell_id in enumerate(self._ordered_cell_ids())
        ]

    def snapshot(
        self,
        *,
        include_source: bool = True,
        include_empty_outputs: bool = False,
        include_internal_cells: bool = False,
        on_error: str = "raise",
    ) -> NotebookSnapshot:
        """Return ordered display outputs for the active scenario."""

        cells = [
            self._snapshot_cell(
                cell,
                include_source=include_source,
                on_error=on_error,
            )
            for cell in self.cells()
            if include_internal_cells or not _is_internal_cell(cell)
        ]
        if not include_empty_outputs:
            cells = [cell for cell in cells if cell.outputs]

        return NotebookSnapshot(
            schema="marimo.notebook.linear.v1",
            version=1,
            kind="notebook",
            notebook=json_object(self.notebook.metadata()),
            cells=cells,
        )

    def report(
        self,
        cells: list[Mapping[str, Any]],
        *,
        include_source: bool = True,
        on_error: str = "record",
    ) -> NotebookSnapshot:
        """Return selected display outputs as an ordered report snapshot."""

        selected: list[CellSnapshot] = []
        for position, item in enumerate(cells):
            cell = self._select_report_cell(item)
            label = item.get("label")
            order = item.get("order", position)
            selected.append(
                self._snapshot_cell(
                    cell,
                    include_source=include_source,
                    on_error=on_error,
                    label=str(label) if label is not None else None,
                    order=int(order) if order is not None else None,
                )
            )

        selected.sort(
            key=lambda cell: cell.order if cell.order is not None else cell.index
        )
        return NotebookSnapshot(
            schema="marimo.report.v1",
            version=1,
            kind="report",
            notebook=json_object(self.notebook.metadata()),
            cells=selected,
        )

    def cell(
        self,
        selector: int | str | None = None,
        *,
        index: int | None = None,
        id: str | None = None,
        name: str | None = None,
    ) -> RuntimeCell:
        """Select one cell by index, id, or unique name.

        ``cell(3)`` is shorthand for ``cell(index=3)``.
        ``cell("intro")`` is shorthand for ``cell(name="intro")``.
        """

        selector_count = sum(value is not None for value in (selector, index, id, name))
        if selector_count != 1:
            raise TypeError("select exactly one of selector, index, id, or name")

        if isinstance(selector, int):
            return self._cell_by_index(selector)
        if isinstance(selector, str):
            return self._cell_by_name(selector)
        if index is not None:
            return self._cell_by_index(index)
        if id is not None:
            return self._cell_by_id(id)
        if name is not None:
            return self._cell_by_name(name)

        raise TypeError("select exactly one of selector, index, id, or name")

    @property
    def runtime(self) -> RuntimeContext:
        """The underlying marimo runtime."""

        return self._runtime

    def _snapshot_cell(
        self,
        cell: RuntimeCell,
        *,
        include_source: bool,
        on_error: str,
        label: str | None = None,
        order: int | None = None,
    ) -> CellSnapshot:
        output = _format_cell_output(cell, on_error=on_error)
        return CellSnapshot(
            index=cell.index,
            id=cell.id,
            name=cell.name,
            defs=cell.defs,
            refs=cell.refs,
            config=json_object(cell.config),
            source=cell.source if include_source else None,
            outputs=[] if output is None else [output],
            label=label,
            order=order,
        )

    def _select_report_cell(self, item: Mapping[str, Any]) -> RuntimeCell:
        selectors = {
            key: item[key]
            for key in ("id", "name", "index")
            if key in item and item[key] is not None
        }
        if len(selectors) != 1:
            raise ValueError("report cells must set exactly one of id, name, or index")
        if "id" in selectors:
            return self.cell(id=str(selectors["id"]))
        if "name" in selectors:
            return self.cell(name=str(selectors["name"]))
        return self.cell(index=int(selectors["index"]))

    def _cell_by_index(self, index: int) -> RuntimeCell:
        cells = self.cells()
        try:
            return cells[index]
        except IndexError as exc:
            raise IndexError(f"cell index {index} is out of range") from exc

    def _cell_by_id(self, cell_id: str) -> RuntimeCell:
        for cell in self.cells():
            if cell.id == cell_id:
                return cell
        raise KeyError(f"cell id {cell_id!r} was not found")

    def _cell_by_name(self, name: str) -> RuntimeCell:
        matches = [cell for cell in self.cells() if cell.name == name]
        if not matches:
            raise KeyError(f"cell name {name!r} was not found")
        if len(matches) > 1:
            raise ValueError(f"cell name {name!r} is not unique")
        return matches[0]

    def _ordered_cell_ids(self) -> list[CellId_t]:
        manager = self._cell_manager()
        if manager is not None:
            return list(manager.valid_cell_ids())
        document = self._notebook_document()
        if document is not None:
            graph_ids = set(self._runtime.graph.cells)
            return [cell.id for cell in document.cells if cell.id in graph_ids]
        return list(self._runtime.graph.cells)

    def _cell_name(self, cell_id: CellId_t) -> str | None:
        manager = self._cell_manager()
        if manager is not None:
            name = _public_cell_name(manager.cell_name(cell_id))
            if name is not None:
                return name

        name = self._document_cell_name(cell_id)
        if name is not None:
            return name

        return self._source_cell_name(cell_id)

    def _cell_manager(self) -> Any | None:
        try:
            app = self._runtime.app
        except (AssertionError, AttributeError):
            return None
        return getattr(app, "cell_manager", None)

    def _notebook_document(self) -> NotebookDocument | None:
        document = get_current_document()
        if document is None:
            return None

        graph_ids = set(self._runtime.graph.cells)
        if not any(cell.id in graph_ids for cell in document.cells):
            return None
        return document

    def _document_cell_name(self, cell_id: CellId_t) -> str | None:
        document = self._notebook_document()
        if document is None:
            return None
        cell = document.get(cell_id)
        if cell is None:
            return None
        return _public_cell_name(cell.name)

    def _source_cell_name(self, cell_id: CellId_t) -> str | None:
        return self._source_cell_names_by_id.get(cell_id)

    @cached_property
    def _source_cell_names_by_id(self) -> dict[CellId_t, str]:
        """Map runtime cells to source-level function names by exact source.

        Active marimo runtimes expose cell names through the in-memory notebook
        document. This saved-file fallback is intentionally stricter: it only
        maps names to runtime cells whose authored source matches exactly.
        Positional matching is unsafe because unsaved edits can leave stale or
        deleted cells in the document/file that are no longer in the graph.
        """

        path = self.notebook.path
        if path is None:
            return {}

        try:
            parsed = get_notebook_status(path)
        except (OSError, SyntaxError):
            return {}
        if parsed.notebook is None:
            return {}

        runtime_cells_by_source: dict[str, list[CellId_t]] = {}
        for cell_id, cell in self._runtime.graph.cells.items():
            runtime_cells_by_source.setdefault(cell.code, []).append(cell_id)

        names_by_id: dict[CellId_t, str] = {}
        used: set[CellId_t] = set()
        for cell in parsed.notebook.cells:
            name = _public_cell_name(cell.name)
            if name is None:
                continue
            candidates = [
                cell_id
                for cell_id in runtime_cells_by_source.get(cell.code, [])
                if cell_id not in used
            ]
            if len(candidates) != 1:
                continue
            cell_id = candidates[0]
            names_by_id[cell_id] = name
            used.add(cell_id)

        return names_by_id


class RuntimeNotebook:
    """File provenance for the current notebook."""

    def __init__(self, runtime: RuntimeContext) -> None:
        self._runtime = runtime

    @property
    def path(self) -> str | None:
        return self._runtime.filename

    @property
    def name(self) -> str | None:
        return None if self.path is None else Path(self.path).name

    @property
    def source(self) -> str | None:
        if self.path is None:
            return None
        return Path(self.path).read_text(encoding="utf-8")

    @property
    def sha256(self) -> str | None:
        source = self.source
        if source is None:
            return None
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    def metadata(self) -> dict[str, str | None]:
        """Return JSON-shaped notebook provenance metadata."""

        return {
            "path": self.path,
            "name": self.name,
            "sha256": self.sha256,
        }


class RuntimeCell:
    """Read-only handle to a marimo cell."""

    def __init__(
        self,
        *,
        notebook: NotebookRuntime,
        cell_id: CellId_t,
        index: int,
    ) -> None:
        self._notebook = notebook
        self._cell_id = cell_id
        self.index = index

    @property
    def id(self) -> str:
        return str(self._cell_id)

    @property
    def name(self) -> str | None:
        return self._notebook._cell_name(self._cell_id)

    @property
    def source(self) -> str:
        """Authored Python source for this cell."""

        return self._cell.code

    @property
    def output(self) -> Any:
        active = self._notebook._evaluation or _ACTIVE_RUNTIME.get()
        if active is not None:
            return active.cell_output(self._cell_id)
        return materialize_cell_output(self._notebook.runtime, self._cell_id)

    def scenario_output(self, *, on_error: str = "raise") -> Any:
        """Return the scenario-local output with an explicit error policy."""

        value = self.output
        _raise_output_snapshot_if_needed(value, on_error=on_error)
        return value

    def stored_output(self, *, on_error: str = "raise") -> Any:
        """Return the stored runtime output without scenario-local values."""

        try:
            return materialize_cell_output(self._notebook.runtime, self._cell_id)
        except Exception as exc:
            if on_error != "record":
                raise
            return _output_error_snapshot(exc)

    @property
    def defs(self) -> list[str]:
        return sorted(self._cell.defs)

    @property
    def refs(self) -> list[str]:
        return sorted(self._cell.refs)

    @property
    def config(self) -> dict[str, Any]:
        return self._cell.config.asdict()

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "id": self.id,
            "name": self.name,
            "defs": self.defs,
            "refs": self.refs,
            "config": self.config,
        }

    @property
    def _cell(self) -> CellImpl:
        return self._notebook.runtime.graph.cells[self._cell_id]


def materialize_cell_output(
    runtime: RuntimeContext,
    cell_id: CellId_t,
    *,
    values: Mapping[str, Any] | None = None,
) -> Any:
    """Return the visible output for a runtime cell.

    Marimo only stores references for outputs that need to stay alive, such as
    UI elements and descriptor-backed widgets. Ordinary markdown/HTML output is
    broadcast to the frontend but not retained on ``CellImpl._output``. For
    those cells, evaluate the compiled display expression against runtime globals,
    plus optional scenario-local values produced by ``mox.evaluate``.
    """

    cell = runtime.graph.cells[cell_id]
    stored_output = cell._output.output
    if values is None and (stored_output is not None or cell.last_expr is None):
        return stored_output
    if cell.last_expr is None:
        return stored_output

    output_globals = dict(runtime.globals)
    if values is not None:
        output_globals.update(values)
    output_globals.update(expression_globals())

    with runtime.with_cell_id(cell_id):
        return eval(cell.last_expr, output_globals)


def _format_cell_output(
    cell: RuntimeCell,
    *,
    on_error: str,
) -> OutputSnapshot | None:
    try:
        value = cell.output
    except Exception as exc:
        if on_error != "record":
            raise
        return _output_error_snapshot(exc)
    if isinstance(value, OutputSnapshot):
        _raise_output_snapshot_if_needed(value, on_error=on_error)
        return value
    return format_output(value, on_error=on_error)


def _raise_output_snapshot_if_needed(value: Any, *, on_error: str) -> None:
    if not isinstance(value, OutputSnapshot):
        return
    if value.channel != "error" or on_error == "record":
        return
    data = value.data
    message = data.get("message") if isinstance(data, Mapping) else data
    raise RuntimeError(f"cell output materialization failed: {message}")


def _output_error_snapshot(exc: Exception) -> OutputSnapshot:
    return OutputSnapshot(
        channel="error",
        mimetype="application/vnd.marimo.export.error+json",
        data={
            "type": type(exc).__name__,
            "message": str(exc),
        },
    )


def _is_internal_cell(cell: RuntimeCell) -> bool:
    return str(cell.name or "").startswith("_moexport_")


def _public_cell_name(name: str | None) -> str | None:
    if name is None or name in {DEFAULT_CELL_NAME, "__"}:
        return None
    if name.startswith(TOPLEVEL_CELL_PREFIX):
        name = name.removeprefix(TOPLEVEL_CELL_PREFIX)
    return name or None


def selected_output_cell_ids(
    expression: str,
    runtime: RuntimeContext,
) -> set[CellId_t]:
    """Find literal ``mox.runtime().cell(...).output`` selectors.

    The evaluator uses this as a planning hint so selected output cells are
    materialized before the user's expression reads them.
    """

    tree = ast.parse(expression, mode="eval")
    notebook = NotebookRuntime(runtime)
    cell_ids: set[CellId_t] = set()

    for node in ast.walk(tree):
        if _is_runtime_snapshot_call(node):
            return {cell._cell_id for cell in notebook.cells()}
        if not _is_cell_output_attribute(node):
            continue
        if not isinstance(node, ast.Attribute):
            continue
        call = node.value
        selector = _literal_cell_selector(call)
        if selector is None:
            continue
        cell_ids.add(_select_cell(notebook, selector)._cell_id)

    return cell_ids


def _is_runtime_snapshot_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and not node.args
        and not node.keywords
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "snapshot"
        and isinstance(node.func.value, ast.Call)
        and _is_runtime_call(node.func.value)
    )


def _is_runtime_call(call: ast.Call) -> bool:
    return (
        not call.args
        and not call.keywords
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "runtime"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id in {"mox", "moexport"}
    )


def _is_cell_output_attribute(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "output"
        and isinstance(node.value, ast.Call)
        and _is_runtime_cell_call(node.value)
    )


def _is_runtime_cell_call(call: Any) -> bool:
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr != "cell":
        return False

    runtime_call = func.value
    if not isinstance(runtime_call, ast.Call):
        return False
    if runtime_call.args or runtime_call.keywords:
        return False

    runtime_func = runtime_call.func
    return (
        isinstance(runtime_func, ast.Attribute)
        and runtime_func.attr == "runtime"
        and isinstance(runtime_func.value, ast.Name)
        and runtime_func.value.id in {"mox", "moexport"}
    )


def _literal_cell_selector(call: Any) -> dict[str, int | str] | None:
    selectors: dict[str, int | str] = {}
    if len(call.args) > 1:
        return None
    if call.args:
        value = _literal(call.args[0])
        if isinstance(value, int):
            selectors["index"] = value
        elif isinstance(value, str):
            selectors["name"] = value
        else:
            return None

    for keyword in call.keywords:
        if keyword.arg not in {"index", "id", "name"}:
            return None
        value = _literal(keyword.value)
        if not isinstance(value, int | str):
            return None
        selectors[keyword.arg] = value

    if len(selectors) != 1:
        return None
    return selectors


def _literal(node: Any) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    return None


def _select_cell(
    notebook: NotebookRuntime,
    selector: dict[str, int | str],
) -> RuntimeCell:
    if "index" in selector:
        return notebook.cell(index=int(selector["index"]))
    if "id" in selector:
        return notebook.cell(id=str(selector["id"]))
    return notebook.cell(name=str(selector["name"]))
