from __future__ import annotations

import ast
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import Any, Protocol

from marimo._ast.variables import is_local
from marimo._code_mode import get_context
from marimo._messaging.cell_output import CellChannel, CellOutput
from marimo._messaging.notification_utils import CellNotificationUtils
from marimo._output import formatting
from marimo._plugins.ui._core.ui_element import UIElement
from marimo._runtime.control_flow import MarimoStopError
from marimo._runtime.runner.hooks import Priority
from marimo._types.ids import CellId_t

from marimo_export._json import (
    JsonObject,
    JsonValue,
    canonical_bytes,
    json_object,
    json_value,
    sha256_bytes,
)
from marimo_export.errors import SelectionError
from marimo_export.spec import Source

_CONTROL_DOMAIN_KEYS = frozenset(
    {
        "allow-select-none",
        "max-selections",
        "options",
        "precision",
        "start",
        "step",
        "steps",
        "stop",
    }
)
_MAX_PYTHON_TYPE_BYTES = 512


class _CodeModeContext(Protocol):
    @property
    def globals(self) -> dict[str, Any]: ...

    @property
    def cells(self) -> Any: ...

    @property
    def _kernel(self) -> Any: ...

    async def __aenter__(self) -> _CodeModeContext: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None: ...

    def set_ui_value(self, element: Any, value: Any) -> None: ...

    def run_cell(self, target: str) -> None: ...


@dataclass(frozen=True, slots=True)
class NotebookInspection:
    filename: str | None
    path: str | None
    document_sha256: str

    def wire(self) -> JsonObject:
        return {
            "filename": self.filename,
            "path": self.path,
            "document_sha256": self.document_sha256,
        }


@dataclass(frozen=True, slots=True)
class CellInspection:
    id: str
    name: str | None
    status: str | None
    has_output: bool
    media_type: str | None

    def wire(self) -> JsonObject:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "has_output": self.has_output,
            "media_type": self.media_type,
        }


@dataclass(frozen=True, slots=True)
class GlobalInspection:
    name: str
    python_type: str

    def wire(self) -> JsonObject:
        return {
            "name": self.name,
            "python_type": self.python_type,
        }


@dataclass(frozen=True, slots=True)
class ControlInspection:
    name: str
    type: str
    value: JsonValue
    sensitive: bool
    domain: JsonObject

    def wire(self) -> JsonObject:
        return json_object(
            {
                "name": self.name,
                "type": self.type,
                "value": json_value(self.value, f"control {self.name!r} value"),
                "sensitive": self.sensitive,
                "domain": self.domain,
            },
            f"control {self.name!r}",
        )


@dataclass(frozen=True, slots=True)
class LiveInspection:
    notebook: NotebookInspection
    globals: tuple[GlobalInspection, ...]
    cells: tuple[CellInspection, ...]
    controls: tuple[ControlInspection, ...]

    def __post_init__(self) -> None:
        global_values = tuple(sorted(self.globals, key=lambda value: value.name))
        names = [value.name for value in global_values]
        if len(names) != len(set(names)):
            raise ValueError("live inspection globals must have unique names")
        object.__setattr__(self, "globals", global_values)

    def wire(self) -> JsonObject:
        return json_object(
            {
                "notebook": self.notebook.wire(),
                "globals": [global_value.wire() for global_value in self.globals],
                "cells": [cell.wire() for cell in self.cells],
                "controls": [control.wire() for control in self.controls],
            },
            "live inspection",
        )


@dataclass(frozen=True, slots=True)
class ControlSnapshot:
    values: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        values = _control_values(self.values, "control snapshot")
        object.__setattr__(self, "values", MappingProxyType(values))


@dataclass(frozen=True, slots=True)
class AppliedControls:
    values: Mapping[str, JsonValue]
    outputs: Mapping[str, object]

    def __post_init__(self) -> None:
        values = _control_values(self.values, "applied controls")
        object.__setattr__(self, "values", MappingProxyType(values))
        object.__setattr__(self, "outputs", MappingProxyType(dict(self.outputs)))


@dataclass(frozen=True, slots=True)
class CellStateSnapshot:
    stale: frozenset[CellId_t]


async def inspect_live() -> LiveInspection:
    """Inspect selectable state in the running notebook kernel."""

    async with get_context() as ctx:
        filename = _notebook_path(ctx)
        cells = tuple(_inspect_cell(cell) for cell in ctx.cells)
        controls = tuple(
            _inspect_control(name, value) for name, value in _direct_controls(ctx).items()
        )
        return LiveInspection(
            notebook=NotebookInspection(
                filename=Path(filename).name if filename is not None else None,
                path=filename,
                document_sha256=document_sha256(ctx),
            ),
            globals=tuple(_selectable_globals(ctx)),
            cells=cells,
            controls=controls,
        )


def document_sha256(ctx: _CodeModeContext) -> str:
    """Hash the ordered authored notebook document."""

    cells: list[JsonObject] = []
    for cell in ctx.cells:
        config = json_object(cell.config.asdict(), f"cell {cell.id!s} config")
        cells.append(
            {
                "id": str(cell.id),
                "code": cell.code,
                "name": cell.name,
                "config": config,
            }
        )
    return sha256_bytes(canonical_bytes(cells))


def resolve_source(
    source: Source,
    ctx: _CodeModeContext,
    fresh_outputs: Mapping[str, object] | None = None,
) -> object:
    """Resolve one validated source against live notebook state."""

    if not isinstance(source, Source):
        raise TypeError("source must be a Source")

    if source.kind == "global":
        try:
            return ctx.globals[source.value]
        except KeyError as error:
            raise SelectionError(
                f"notebook global {source.value!r} is unavailable",
                details={"kind": "global", "source": source.value},
            ) from error

    if source.kind == "expression":
        try:
            code = compile(source.value, "<marimo-export-expression>", "eval")
            return eval(code, ctx.globals, ctx.globals)
        except Exception as error:
            raise SelectionError(
                f"expression {source.value!r} failed with {type(error).__name__}: {error}",
                details={"kind": "expression", "source": source.value},
            ) from error

    if source.kind == "cell":
        try:
            cell = ctx.cells[source.value]
        except (KeyError, IndexError) as error:
            raise SelectionError(
                f"notebook cell {source.value!r} is unavailable",
                details={"kind": "cell", "source": source.value},
            ) from error

        if fresh_outputs is not None:
            candidates = (source.value, str(cell.id), cell.name)
            for candidate in candidates:
                if candidate and candidate in fresh_outputs:
                    return fresh_outputs[candidate]

        output = cell.output
        if output is None:
            raise SelectionError(
                f"notebook cell {source.value!r} has no rendered output",
                details={"kind": "cell", "source": source.value},
            )
        return output

    raise TypeError(f"unsupported source kind: {source.kind!r}")


def preflight_named_sources(
    sources: Iterable[Source],
    inspection: LiveInspection,
) -> None:
    """Validate named sources without evaluating variant-time expressions."""

    selected_sources = _sources(sources)
    global_names = {global_value.name for global_value in inspection.globals}
    cell_names = {cell.id for cell in inspection.cells}
    cell_names.update(cell.name for cell in inspection.cells if cell.name is not None)
    for source in selected_sources:
        if source.kind == "global" and source.value not in global_names:
            raise SelectionError(
                f"notebook global {source.value!r} is unavailable",
                details={"kind": "global", "source": source.value},
            )
        if source.kind == "cell" and source.value not in cell_names:
            raise SelectionError(
                f"notebook cell {source.value!r} is unavailable",
                details={"kind": "cell", "source": source.value},
            )


async def snapshot_controls(names: Iterable[str]) -> ControlSnapshot:
    """Capture frontend values for direct named UI controls."""

    requested = _control_names(names)
    async with get_context() as ctx:
        controls = _resolve_controls(ctx, requested)
        return ControlSnapshot(
            {
                name: _frontend_value(control, f"control {name!r}")
                for name, control in controls.items()
            }
        )


def snapshot_cell_state() -> CellStateSnapshot:
    """Capture the authored cells that are stale before live export."""

    graph = get_context()._kernel.graph
    return CellStateSnapshot(stale=frozenset(graph.get_stale()))


async def apply_controls(
    values: Mapping[str, object],
    sources: Iterable[Source] = (),
) -> AppliedControls:
    """Apply one UI vector as a batch and collect reactive cell results."""

    requested = _control_values(values, "control values")
    selected_sources = _sources(sources)

    ctx = get_context()
    controls = _resolve_controls(ctx, tuple(requested))
    kernel = ctx._kernel
    original_hooks = kernel._hooks
    capture_hooks = original_hooks.copy()
    outputs: dict[str, object] = {}
    errors: dict[str, object] = {}
    hook_failures: list[BaseException] = []
    names_by_id = {str(cell.id): cell.name for cell in ctx.cells if cell.name}

    def capture_result(cell: Any, hook_ctx: Any, run_result: Any) -> None:
        del hook_ctx
        try:
            cell_id = str(cell.cell_id)
            name = names_by_id.get(cell_id)
            if run_result.exception is not None and not isinstance(
                run_result.exception,
                MarimoStopError,
            ):
                errors[cell_id] = run_result.exception
                outputs.pop(cell_id, None)
                if name is not None:
                    outputs.pop(name, None)
                return
            errors.pop(cell_id, None)
            value = run_result.output
            if value is None and run_result.accumulated_output:
                value = run_result.accumulated_output.stack()
            output = _rendered_output(value)
            outputs[cell_id] = output
            if name is not None:
                outputs[name] = output
        except BaseException as error:
            hook_failures.append(error)

    capture_hooks.add_post_execution(capture_result, Priority.LATE)
    kernel._hooks = capture_hooks
    try:
        changed = {
            name: value
            for name, value in requested.items()
            if _frontend_value(controls[name], f"control {name!r}") != value
        }
        if changed:
            async with ctx:
                for name, value in changed.items():
                    ctx.set_ui_value(controls[name], value)

        if kernel.reactive_execution_mode == "lazy" and selected_sources:
            targets = _stale_source_targets(ctx, selected_sources)
            if targets:
                async with ctx:
                    for target in targets:
                        ctx.run_cell(target)
                remaining = _stale_targets(ctx, targets)
                if remaining:
                    raise SelectionError(
                        "selected notebook sources remain stale after execution",
                        details={"cell_ids": remaining},
                    )
    finally:
        if kernel._hooks is capture_hooks:
            kernel._hooks = original_hooks

    if hook_failures:
        raise SelectionError("could not capture reactive notebook outputs") from hook_failures[0]

    if errors:
        cell_id, error = next(iter(errors.items()))
        name = names_by_id.get(cell_id)
        label = f"{name!r} ({cell_id})" if name is not None else cell_id
        raise SelectionError(
            f"reactive cell {label} failed with {type(error).__name__}: {error}",
            details={
                "cell_id": cell_id,
                "cell_name": name,
                "exception_type": type(error).__name__,
            },
        )

    for name, expected in requested.items():
        actual = _frontend_value(controls[name], f"control {name!r}")
        if actual != expected:
            raise SelectionError(
                f"control {name!r} did not accept the requested value",
                details={"control": name},
            )

    return AppliedControls(values=requested, outputs=outputs)


async def restore_controls(
    snapshot: ControlSnapshot,
    sources: Iterable[Source] = (),
) -> AppliedControls:
    """Restore a previously captured UI vector as one batch."""

    if not isinstance(snapshot, ControlSnapshot):
        raise TypeError("snapshot must be a ControlSnapshot")
    return await apply_controls(snapshot.values, sources)


async def restore_cell_state(snapshot: CellStateSnapshot) -> None:
    """Restore the cell-staleness boundary captured before live export."""

    if not isinstance(snapshot, CellStateSnapshot):
        raise TypeError("snapshot must be a CellStateSnapshot")
    ctx = get_context()
    graph = ctx._kernel.graph
    current = set(graph.get_stale())
    targets = current - snapshot.stale
    if targets:
        ordered = [str(cell.id) for cell in ctx.cells if cell.id in targets]
        async with ctx:
            for target in ordered:
                ctx.run_cell(target)

    failed = sorted(
        str(cell_id)
        for cell_id in targets
        if graph.cells[cell_id].run_result_status in {"cancelled", "exception", "marimo-error"}
    )

    if snapshot.stale:
        graph.set_stale(set(snapshot.stale))
    restored = set(graph.get_stale())
    if restored != snapshot.stale:
        raise SelectionError(
            "could not restore notebook cell state after capture",
            details={
                "expected_stale": sorted(map(str, snapshot.stale)),
                "actual_stale": sorted(map(str, restored)),
            },
        )
    if failed:
        raise SelectionError(
            "authored cells failed while restoring notebook state",
            details={"cell_ids": failed},
        )


def _sources(values: Iterable[Source]) -> tuple[Source, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError("sources must be an iterable of Source values")
    result = tuple(values)
    if any(not isinstance(source, Source) for source in result):
        raise TypeError("sources must contain Source values")
    return result


def _stale_source_targets(
    ctx: _CodeModeContext,
    sources: tuple[Source, ...],
) -> tuple[str, ...]:
    graph = ctx._kernel.graph
    candidates: set[object] = set()
    for source in sources:
        if source.kind == "cell":
            try:
                candidates.add(ctx.cells[source.value].id)
            except (KeyError, IndexError) as error:
                raise SelectionError(
                    f"notebook cell {source.value!r} is unavailable",
                    details={"kind": "cell", "source": source.value},
                ) from error
            continue

        names = {source.value} if source.kind == "global" else _expression_names(source.value)
        for name in names:
            candidates.update(graph.get_defining_cells(name))

    ordered = [
        str(cell.id) for cell in ctx.cells if cell.id in candidates and graph.cells[cell.id].stale
    ]
    return tuple(ordered)


def _stale_targets(
    ctx: _CodeModeContext,
    targets: tuple[str, ...],
) -> list[str]:
    graph = ctx._kernel.graph
    return [target for target in targets if graph.cells[target].stale]


def _expression_names(expression: str) -> set[str]:
    parsed = ast.parse(expression, mode="eval")
    return {
        node.id
        for node in ast.walk(parsed)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def _rendered_output(value: object) -> CellOutput:
    formatted = formatting.try_format(value)
    if formatted.exception is not None:
        formatted = formatting.try_format(value, include_opinionated=False)
    if formatted.exception is not None:
        raise formatted.exception
    mimetype, data = CellNotificationUtils.maybe_truncate_output(
        formatted.mimetype,
        formatted.data,
    )
    return CellOutput(
        channel=CellChannel.OUTPUT,
        mimetype=mimetype,
        data=data,
        timestamp=0.0,
    )


def _inspect_cell(cell: Any) -> CellInspection:
    output = cell.output
    media_type = None if output is None else str(output.mimetype)
    return CellInspection(
        id=str(cell.id),
        name=cell.name or None,
        status=str(cell.status) if cell.status is not None else None,
        has_output=output is not None,
        media_type=media_type,
    )


def _inspect_control(name: str, control: UIElement[Any, Any]) -> ControlInspection:
    sensitive = _is_sensitive_control(control)
    return ControlInspection(
        name=name,
        type=type(control).__name__,
        value=None if sensitive else _frontend_value(control, f"control {name!r}"),
        sensitive=sensitive,
        domain=_control_domain(control, sensitive=sensitive),
    )


def _is_sensitive_control(control: UIElement[Any, Any]) -> bool:
    return any(_component_kind(item) == "password" for item in _control_tree(control))


def _component_kind(control: UIElement[Any, Any]) -> object:
    args = getattr(control, "_component_args", None)
    return args.get("kind") if isinstance(args, Mapping) else None


def _control_tree(
    root: UIElement[Any, Any],
) -> Iterable[UIElement[Any, Any]]:
    pending = [root]
    seen: set[int] = set()
    while pending:
        control = pending.pop()
        identity = id(control)
        if identity in seen:
            continue
        seen.add(identity)
        yield control

        element = getattr(control, "element", None)
        if isinstance(element, UIElement):
            pending.append(element)
        elements = getattr(control, "elements", None)
        if isinstance(elements, Mapping):
            pending.extend(item for item in elements.values() if isinstance(item, UIElement))
        elif isinstance(elements, (list, tuple)):
            pending.extend(item for item in elements if isinstance(item, UIElement))


def _control_domain(
    control: UIElement[Any, Any],
    *,
    sensitive: bool,
) -> JsonObject:
    if sensitive:
        return {}
    return _control_domain_tree(control, set())


def _control_domain_tree(
    control: UIElement[Any, Any],
    stack: set[int],
) -> JsonObject:
    identity = id(control)
    if identity in stack:
        return {}
    stack.add(identity)
    try:
        element = getattr(control, "element", None)
        if isinstance(element, UIElement):
            return _control_domain_tree(element, stack)

        elements = getattr(control, "elements", None)
        if isinstance(elements, Mapping):
            children = sorted(
                (
                    (name, item)
                    for name, item in elements.items()
                    if isinstance(name, str) and isinstance(item, UIElement)
                ),
                key=lambda child: child[0],
            )
            return json_object(
                {name: _control_domain_tree(item, stack) for name, item in children},
                "composite control domain",
            )
        if isinstance(elements, (list, tuple)):
            return json_object(
                {
                    str(index): _control_domain_tree(item, stack)
                    for index, item in enumerate(elements)
                    if isinstance(item, UIElement)
                },
                "composite control domain",
            )

        return _direct_control_domain(control)
    finally:
        stack.remove(identity)


def _direct_control_domain(control: UIElement[Any, Any]) -> JsonObject:
    args = getattr(control, "_component_args", None)
    if not isinstance(args, Mapping):
        return {}
    domain: JsonObject = {}
    for name in sorted(_CONTROL_DOMAIN_KEYS):
        if name not in args:
            continue
        try:
            domain[name] = json_value(args[name], f"control domain {name!r}")
        except (TypeError, ValueError):
            continue
    return domain


def _notebook_path(ctx: _CodeModeContext) -> str | None:
    filename = ctx._kernel.app_metadata.filename
    if filename is None:
        return None
    if not isinstance(filename, str) or not filename:
        raise SelectionError("the running notebook has an invalid filename")
    return filename


def _selectable_globals(ctx: _CodeModeContext) -> list[GlobalInspection]:
    names = sorted(
        name
        for name in ctx.globals
        if isinstance(name, str)
        and name.isidentifier()
        and name != "__builtins__"
        and not is_local(name)
    )
    return [
        GlobalInspection(name=name, python_type=_python_type(ctx.globals[name])) for name in names
    ]


def _python_type(value: object) -> str:
    concrete_type = type(value)
    module = type.__getattribute__(concrete_type, "__module__")
    qualname = type.__getattribute__(concrete_type, "__qualname__")
    escaped_module = _escape_type_part(module, "unknown")
    escaped_qualname = _escape_type_part(qualname, "unknown")
    descriptor = f"{escaped_module}.{escaped_qualname}"
    encoded = descriptor.encode("utf-8")
    if len(encoded) <= _MAX_PYTHON_TYPE_BYTES:
        return descriptor

    suffix = f"#sha256:{sha256_bytes(encoded)}"
    maximum_prefix_bytes = _MAX_PYTHON_TYPE_BYTES - len(suffix)
    prefix = encoded[:maximum_prefix_bytes]
    while True:
        try:
            return f"{prefix.decode('utf-8')}{suffix}"
        except UnicodeDecodeError:
            prefix = prefix[:-1]


def _escape_type_part(value: object, fallback: str) -> str:
    if not isinstance(value, str) or not value:
        return fallback
    escaped: list[str] = []
    for character in value:
        codepoint = ord(character)
        if character == "\\":
            escaped.append("\\\\")
        elif character.isspace() or unicodedata.category(character) in {
            "Cc",
            "Cf",
            "Cs",
        }:
            escaped.append(f"\\u{codepoint:04x}" if codepoint <= 0xFFFF else f"\\U{codepoint:08x}")
        else:
            escaped.append(character)
    return "".join(escaped)


def _direct_controls(
    ctx: _CodeModeContext,
) -> dict[str, UIElement[Any, Any]]:
    return {
        name: value
        for name, value in sorted(ctx.globals.items())
        if isinstance(name, str)
        and name.isidentifier()
        and not is_local(name)
        and isinstance(value, UIElement)
    }


def _resolve_controls(
    ctx: _CodeModeContext,
    names: Iterable[str],
) -> dict[str, UIElement[Any, Any]]:
    result: dict[str, UIElement[Any, Any]] = {}
    for name in names:
        value = ctx.globals.get(name)
        if not isinstance(value, UIElement):
            raise SelectionError(
                f"notebook global {name!r} is not a direct UI control",
                details={"control": name},
            )
        result[name] = value
    return result


def _control_names(names: Iterable[str]) -> tuple[str, ...]:
    if isinstance(names, (str, bytes)):
        raise TypeError("control names must be an iterable of names")
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        _control_name(name)
        if name in seen:
            raise SelectionError(f"control name {name!r} is repeated")
        seen.add(name)
        result.append(name)
    return tuple(result)


def _control_values(value: object, path: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise TypeError(f"{path} must be an object")
    result: JsonObject = {}
    for raw_name, item in value.items():
        name = _control_name(raw_name)
        result[name] = json_value(item, f"{path}.{name}")
    return result


def _control_name(value: object) -> str:
    if not isinstance(value, str) or not value.isidentifier():
        raise SelectionError("control names must be Python identifiers")
    return value


def _frontend_value(control: UIElement[Any, Any], path: str) -> JsonValue:
    try:
        value = control._value_frontend
    except AttributeError as error:
        raise SelectionError(f"{path} has no frontend value") from error
    try:
        return json_value(value, f"{path} frontend value")
    except (TypeError, ValueError) as error:
        raise SelectionError(f"{path} has a non-portable frontend value") from error


__all__ = [
    "AppliedControls",
    "CellInspection",
    "CellStateSnapshot",
    "ControlInspection",
    "ControlSnapshot",
    "GlobalInspection",
    "LiveInspection",
    "NotebookInspection",
    "apply_controls",
    "document_sha256",
    "inspect_live",
    "preflight_named_sources",
    "resolve_source",
    "restore_cell_state",
    "restore_controls",
    "snapshot_cell_state",
    "snapshot_controls",
]
