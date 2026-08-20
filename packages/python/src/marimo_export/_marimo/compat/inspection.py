"""Inspect the attached marimo kernel through stable export records."""

from __future__ import annotations

import ast
import hashlib
import unicodedata
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from marimo_export._cell_ids import canonical_cell_id
from marimo_export._diagnostics import safe_diagnostic
from marimo_export._execution.plan import Baseline, CellDefinition, Definition
from marimo_export._json import (
    JsonObject,
    JsonValue,
    canonical_bytes,
    json_object,
    json_value,
    portable_json_value,
)
from marimo_export._marimo.capabilities import MarimoCapabilities
from marimo_export.errors import CompatibilityError, ExecutionError
from marimo_export.index import (
    ControlElementStep,
    ControlIndexStep,
    ControlKeyStep,
    ControlPathStep,
)

_CAPABILITIES = (
    "asset_transfer",
    "blob_asset",
    "cache_cells",
    "cell_cache_receipts",
    "child_sessions",
    "child_ui_updates",
    "definition_overrides",
    "projection_snapshots",
    "setup_definition_overrides",
    "synthetic_output_cells",
)
_MAX_PYTHON_TYPE_BYTES = 512
_PARENT_STOP_PROVENANCE_ATTRIBUTE = "_marimo_export_parent_stop_provenance"

if TYPE_CHECKING:
    from marimo_export.integration import KernelInputObservation


def runtime_path() -> str | None:
    """Return the selected notebook path from the attached runtime."""

    from marimo._runtime.context import get_context

    value = get_context().filename
    return value if isinstance(value, str) and value else None


def validate_parent_state() -> None:
    """Reject a completed authored run that left a failed notebook cell."""

    from marimo._runtime.context import get_context
    from marimo._runtime.context.kernel_context import KernelRuntimeContext
    from marimo._runtime.dataflow import topological_sort

    context = get_context()
    if not isinstance(context, KernelRuntimeContext):
        raise CompatibilityError(
            "managed baseline validation requires a file-backed Marimo kernel",
            code="marimo_incompatible",
        )
    graph = context._kernel.graph
    from marimo_export._marimo.compat.child_run import StopProvenance

    provenance = getattr(context, _PARENT_STOP_PROVENANCE_ATTRIBUTE, None)
    for cell_id in topological_sort(graph, set(graph.cells)):
        cell = graph.cells[cell_id]
        status = cell.run_result_status
        if status not in {"exception", "cancelled", "marimo-error"}:
            continue
        stopping_cell = (
            provenance.stopping_cell(cell_id) if isinstance(provenance, StopProvenance) else None
        )
        if (status == "exception" and stopping_cell == cell_id) or (
            status == "cancelled" and stopping_cell is not None
        ):
            continue
        exception = cell.exception
        exception_type = safe_diagnostic(
            type(exception).__name__ if exception is not None else status,
            maximum_chars=_MAX_PYTHON_TYPE_BYTES,
        )
        source_cell_id = canonical_cell_id(cell_id)
        raise ExecutionError(
            f"the notebook initial run failed in cell {source_cell_id!r} with {exception_type}",
            code="state_execution_failed",
            details={
                "cell_id": source_cell_id,
                "exception_type": exception_type,
                "status": status,
            },
        ) from exception


def install_parent_stop_provenance(context: object) -> Callable[[], None]:
    """Record exact stop and cancellation outcomes for managed validation."""

    from marimo._runtime.context.kernel_context import KernelRuntimeContext
    from marimo._runtime.runner.hooks import Priority

    from marimo_export._marimo.compat.child_run import StopProvenance

    if not isinstance(context, KernelRuntimeContext):
        raise TypeError("context must be a file-backed Marimo kernel context")
    provenance = StopProvenance()
    graph = context.graph

    def record_cell(cell: Any, hook_context: Any, result: Any) -> None:
        if hook_context.graph is graph:
            provenance.record_cell(cell, hook_context, result)

    def record_finish(hook_context: Any) -> None:
        if hook_context.graph is graph:
            provenance.record_finish(hook_context)

    context._kernel._hooks.add_post_execution(record_cell, priority=Priority.EARLY)
    context._kernel._hooks.add_on_finish(record_finish)
    setattr(context, _PARENT_STOP_PROVENANCE_ATTRIBUTE, provenance)
    released = False

    def release() -> None:
        nonlocal released
        if released:
            return
        released = True
        if getattr(context, _PARENT_STOP_PROVENANCE_ATTRIBUTE, None) is provenance:
            delattr(context, _PARENT_STOP_PROVENANCE_ATTRIBUTE)

    return release


def document_sha256_from_source(notebook: Path, source: bytes) -> str:
    """Return a saved notebook's canonical cell-record digest."""

    from marimo._ast.load import load_notebook_ir
    from marimo._ast.parse import NonMarimoPythonScriptError, is_non_marimo_python_script
    from marimo._session.notebook.serializer import get_notebook_serializer

    try:
        contents = source.decode("utf-8", errors="replace").strip()
        if not contents:
            app = None
        else:
            notebook_ir = get_notebook_serializer(notebook).deserialize(
                contents,
                filepath=str(notebook),
            )
            if notebook_ir and is_non_marimo_python_script(notebook_ir):
                raise NonMarimoPythonScriptError(
                    f"Python script {notebook} is not a Marimo notebook."
                )
            if not notebook_ir.valid:
                app = None
            else:
                app = load_notebook_ir(notebook_ir, filepath=str(notebook))
                app._cell_manager.ensure_one_cell()
    except Exception as error:
        raise ExecutionError(
            "the saved notebook could not be parsed",
            code="notebook_invalid",
            details={"exception_type": type(error).__name__},
        ) from error
    if app is None:
        raise ExecutionError(
            "the saved notebook contains no Marimo application",
            code="notebook_invalid",
        )
    manager = getattr(app, "_cell_manager", None)
    document = getattr(manager, "document", None)
    cells = getattr(document, "cells", None)
    if not isinstance(cells, list):
        raise ExecutionError(
            "the installed Marimo runtime cannot inspect saved notebook cells",
            code="marimo_incompatible",
        )
    return _document_sha256(cells)


def require_capabilities() -> MarimoCapabilities:
    """Validate the focused private seams used by the rewrite."""

    from marimo_export._marimo.compat.cache.probe import require_cache_capabilities

    require_cache_capabilities()
    missing: list[str] = []
    try:
        import marimo
        from marimo._ast.app import InternalApp
        from marimo._ast.load import load_notebook_ir
        from marimo._code_mode import get_context as get_code_context
        from marimo._convert.common.dom_traversal import (
            _PUBLIC_FILE_PATTERN,
            _is_virtual_file_url,
            _parse_virtual_file_url,
            _resolve_public_file,
            replace_html_attributes,
        )
        from marimo._messaging.cell_output import CellOutput
        from marimo._messaging.streams import (
            ThreadSafeStderr,
            ThreadSafeStdout,
            ThreadSafeStream,
        )
        from marimo._messaging.types import KernelStreams
        from marimo._plugins.ui._core.registry import UIElementRegistry
        from marimo._runtime.app.kernel_runner import AppKernelRunner
        from marimo._runtime.context import get_context as get_runtime_context
        from marimo._runtime.context.kernel_context import KernelRuntimeContext
        from marimo._runtime.dataflow import prune_cells_for_overrides
        from marimo._runtime.runner.hooks_post_execution import (
            _broadcast_outputs,
            _flush_console,
            _set_run_result_status,
        )
        from marimo._runtime.virtual_file import read_virtual_file
        from marimo._save.hash import (
            hash_module,
        )
        from marimo._save.stubs import BlobAsset as NativeBlobAsset
        from marimo._save.stubs.lazy_stub import BLOB_DESERIALIZERS, BLOB_SERIALIZERS
        from marimo._schemas.serialization import CellDef, NotebookSerializationV1
        from marimo._session.state.session_view import SessionView
    except ImportError as error:
        raise CompatibilityError(
            "the attached marimo runtime lacks required export capabilities",
            code="marimo_incompatible",
        ) from error

    for value, name in (
        (InternalApp, "child_sessions"),
        (AppKernelRunner, "child_sessions"),
        (load_notebook_ir, "child_sessions"),
        (prune_cells_for_overrides, "definition_overrides"),
        (hash_module, "cell_cache_receipts"),
        (CellDef, "synthetic_output_cells"),
        (NotebookSerializationV1, "child_sessions"),
        (CellOutput, "projection_snapshots"),
        (KernelStreams, "projection_snapshots"),
        (SessionView, "projection_snapshots"),
        (ThreadSafeStderr, "projection_snapshots"),
        (ThreadSafeStdout, "projection_snapshots"),
        (ThreadSafeStream, "projection_snapshots"),
        (UIElementRegistry, "projection_snapshots"),
        (_is_virtual_file_url, "projection_snapshots"),
        (_parse_virtual_file_url, "projection_snapshots"),
        (_resolve_public_file, "projection_snapshots"),
        (_broadcast_outputs, "projection_snapshots"),
        (_flush_console, "projection_snapshots"),
        (_set_run_result_status, "projection_snapshots"),
        (replace_html_attributes, "projection_snapshots"),
        (read_virtual_file, "projection_snapshots"),
    ):
        if not callable(value):
            missing.append(name)
    if not callable(get_code_context):
        missing.append("child_sessions")
    if (
        not callable(getattr(SessionView, "get_model_notifications", None))
        or not callable(getattr(UIElementRegistry, "get_object", None))
        or not callable(getattr(_PUBLIC_FILE_PATTERN, "fullmatch", None))
    ):
        missing.append("projection_snapshots")
    if not _blob_asset_codec(NativeBlobAsset, BLOB_SERIALIZERS, BLOB_DESERIALIZERS):
        missing.append("blob_asset")

    try:
        runtime = get_runtime_context()
    except Exception:
        runtime = None
    if runtime is not None and not isinstance(runtime, KernelRuntimeContext):
        missing.append("child_sessions")

    if missing:
        names = sorted(set(missing))
        raise CompatibilityError(
            "the attached marimo runtime lacks required capabilities: " + ", ".join(names),
            code="marimo_incompatible",
            details={"missing": names},
        )
    return MarimoCapabilities(str(marimo.__version__), _CAPABILITIES)


def _blob_asset_codec(
    blob_asset: type,
    serializers: Mapping[str, object],
    deserializers: Mapping[str, object],
) -> bool:
    serializer = serializers.get("bin")
    deserializer = deserializers.get(".bin")
    if not callable(serializer) or not callable(deserializer):
        return False
    encode = cast(Callable[[object], bytes], serializer)
    decode = cast(Callable[[bytes, object], object], deserializer)
    try:
        value = blob_asset(
            data=b"capability",
            media_type="application/octet-stream",
            filename="capability.bin",
            metadata={"probe": True},
        )
        return decode(encode(value), None) == value
    except Exception:
        return False


async def inspect_baseline() -> Baseline:
    """Read graph ownership and current values from the selected parent."""

    from marimo._code_mode import get_context

    async with get_context() as context:
        graph = context._kernel.graph
        definitions = _inspect_definitions(graph, context.globals)
        ui_definition_names = {
            name for name, definition in definitions.items() if definition.kind == "ui"
        }
        from marimo._ast.names import is_internal_cell_name

        cells = tuple(
            CellDefinition(
                id=canonical_cell_id(cell.id),
                name=(None if not cell.name or is_internal_cell_name(cell.name) else cell.name),
                code_sha256=hashlib.sha256(cell.code.encode("utf-8")).hexdigest(),
                config=json_object(cell.config.asdict(), f"cell {cell.id!s} config"),
                input_dependencies=_cell_ui_dependencies(
                    graph,
                    cell.id,
                    ui_definition_names,
                    include_own=True,
                ),
            )
            for cell in context.cells
        )
        return Baseline(
            definitions=definitions,
            cells=cells,
            document_sha256=_document_sha256(context.cells),
            filename=_portable_filename(context._kernel.app_metadata.filename),
        )


def observe_kernel_inputs(kernel: object) -> KernelInputObservation:
    """Return canonical portable UI roots from one live kernel."""

    from marimo_export._control_roots import ControlRootCandidate, select_control_roots
    from marimo_export.index import ControlBinding
    from marimo_export.integration import KernelInputObservation

    graph = getattr(kernel, "graph", None)
    globals_ = getattr(kernel, "globals", None)
    if graph is None or not isinstance(globals_, Mapping):
        raise TypeError("kernel must expose graph and globals")
    ui_definitions = {
        name: definition
        for name, definition in _inspect_ui_definitions(graph, globals_).items()
        if definition.control_paths
    }
    roots = select_control_roots(
        ControlRootCandidate(
            name=name,
            control_ids=tuple(sorted(definition.control_paths)),
            input_dependencies=definition.input_dependencies,
            eligible=(
                definition.portable_input
                and not definition.sensitive
                and bool(definition.control_paths)
            ),
        )
        for name, definition in ui_definitions.items()
    )
    values: JsonObject = {}
    bindings: dict[str, ControlBinding] = {}
    for name in roots:
        definition = ui_definitions[name]
        if not definition.portable_input or definition.sensitive:
            continue
        values[name] = definition.frontend_value
        for object_id, path in definition.control_paths.items():
            binding = ControlBinding(input=name, path=path)
            previous = bindings.setdefault(object_id, binding)
            if previous != binding:
                raise ExecutionError(f"control {object_id!r} belongs to overlapping input roots")
    return KernelInputObservation(values, bindings)


def _inspect_definitions(
    graph: Any,
    globals_: Mapping[str, object],
) -> dict[str, Definition]:
    definitions = _inspect_ui_definitions(graph, globals_)
    ui_definition_names = set(definitions)
    final_bindings_by_owner: dict[Any, frozenset[str]] = {}
    for name in sorted(graph.definitions):
        if name in definitions:
            continue
        owners = graph.get_defining_cells(name)
        if len(owners) != 1 or name not in globals_:
            continue
        owner = next(iter(owners))
        cell = graph.cells[owner]
        final_bindings = final_bindings_by_owner.get(owner)
        if final_bindings is None:
            final_bindings = _final_expression_bindings(cell.code)
            final_bindings_by_owner[owner] = final_bindings
        value = globals_[name]
        portable_input = True
        try:
            portable_json_value(value, f"definition {name!r}")
        except (TypeError, ValueError):
            portable_input = False
        definitions[name] = Definition(
            name=name,
            cell_id=canonical_cell_id(owner),
            siblings=tuple(sorted(cell.defs)),
            kind="ordinary",
            python_type=_python_type(value),
            value=value,
            portable_input=portable_input,
            final_expression_bound=name in final_bindings,
            input_dependencies=_cell_ui_dependencies(
                graph,
                owner,
                ui_definition_names,
                include_own=False,
            ),
        )
    return {name: definitions[name] for name in sorted(definitions)}


def _inspect_ui_definitions(
    graph: Any,
    globals_: Mapping[str, object],
) -> dict[str, Definition]:
    from marimo._plugins.ui._core.ui_element import UIElement

    ui_definition_names = {
        name
        for name in graph.definitions
        if len(graph.get_defining_cells(name)) == 1 and isinstance(globals_.get(name), UIElement)
    }
    definitions: dict[str, Definition] = {}
    for name in sorted(ui_definition_names):
        owners = graph.get_defining_cells(name)
        owner = next(iter(owners))
        cell = graph.cells[owner]
        value = globals_[name]
        sensitive = _is_sensitive(value)
        frontend_value: JsonValue | None = None
        portable_input = not sensitive
        if not sensitive:
            try:
                frontend_value = _ui_baseline_value(value, f"definition {name!r}")
            except ExecutionError:
                portable_input = False
        control_paths = {str(element._id): path for element, path in _control_tree_entries(value)}
        definitions[name] = Definition(
            name=name,
            cell_id=canonical_cell_id(owner),
            siblings=tuple(sorted(cell.defs)),
            kind="ui",
            python_type=_python_type(value),
            value=value,
            frontend_value=frontend_value,
            portable_input=portable_input,
            ui_patch=_is_anywidget(value),
            sensitive=sensitive,
            domain=_control_domain(value),
            control_paths=control_paths,
            input_dependencies=_cell_ui_dependencies(
                graph,
                owner,
                ui_definition_names,
                include_own=False,
            ),
        )
    return definitions


def _final_expression_bindings(code: str) -> frozenset[str]:
    try:
        module = ast.parse(code)
    except SyntaxError as error:
        raise ExecutionError(
            "an authored cell could not be inspected for state overrides",
            code="notebook_invalid",
        ) from error
    if not module.body or not isinstance(module.body[-1], ast.Expr):
        return frozenset()
    visitor = _NamedExpressionBindings()
    visitor.visit(module.body[-1].value)
    return frozenset(visitor.names)


class _NamedExpressionBindings(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        if isinstance(node.target, ast.Name):
            self.names.add(node.target.id)
        self.visit(node.value)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        del node


def _cell_ui_dependencies(
    graph: Any,
    cell_id: Any,
    ui_definition_names: set[str],
    *,
    include_own: bool,
) -> tuple[str, ...]:
    from marimo._runtime.dataflow import transitive_closure

    cells = transitive_closure(
        graph,
        {cell_id},
        children=False,
        inclusive=True,
    )
    referenced = {
        name
        for ancestor in cells
        for name in graph.cells[ancestor].refs
        if name in ui_definition_names
    }
    if include_own:
        referenced.update(
            name for name in ui_definition_names if cell_id in graph.get_defining_cells(name)
        )
    return tuple(sorted(referenced))


async def declared_ui_values(names: tuple[str, ...]) -> JsonObject:
    from marimo._code_mode import get_context
    from marimo._plugins.ui._core.ui_element import UIElement

    result: JsonObject = {}
    async with get_context() as context:
        for name in names:
            value = context.globals.get(name)
            if isinstance(value, UIElement):
                result[name] = _ui_baseline_value(value, f"definition {name!r}")
    return result


def _document_sha256(cells: Any) -> str:
    from marimo._ast.names import is_internal_cell_name

    value = [
        {
            "id": canonical_cell_id(cell.id),
            "code": cell.code.rstrip(),
            "name": (None if not cell.name or is_internal_cell_name(cell.name) else cell.name),
            "config": json_object(cell.config.asdict(), f"cell {cell.id!s} config"),
        }
        for cell in cells
    ]
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _portable_filename(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ExecutionError("the selected notebook has an invalid filename")
    return Path(value).name


def _python_type(value: object) -> str:
    concrete = type(value)
    module = _escape_type_part(type.__getattribute__(concrete, "__module__"), "unknown")
    qualname = _escape_type_part(type.__getattribute__(concrete, "__qualname__"), "unknown")
    descriptor = f"{module}.{qualname}"
    encoded = descriptor.encode("utf-8")
    if len(encoded) <= _MAX_PYTHON_TYPE_BYTES:
        return descriptor
    suffix = f"#sha256:{hashlib.sha256(encoded).hexdigest()}"
    prefix = encoded[: _MAX_PYTHON_TYPE_BYTES - len(suffix)]
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
        elif character.isspace() or unicodedata.category(character) in {"Cc", "Cf", "Cs"}:
            escaped.append(f"\\u{codepoint:04x}" if codepoint <= 0xFFFF else f"\\U{codepoint:08x}")
        else:
            escaped.append(character)
    return "".join(escaped)


def _frontend_value(value: Any, path: str) -> JsonValue:
    try:
        frontend = value._value_frontend
    except AttributeError as error:
        raise ExecutionError(f"{path} has no frontend value") from error
    try:
        return portable_json_value(frontend, f"{path} frontend value")
    except (TypeError, ValueError) as error:
        raise ExecutionError(f"{path} has a nonportable frontend value") from error


def _ui_baseline_value(value: Any, path: str) -> JsonValue:
    if _is_anywidget(value):
        try:
            return portable_json_value(value.value, f"{path} widget state")
        except (TypeError, ValueError) as error:
            raise ExecutionError(f"{path} has a nonportable widget state") from error
    return _frontend_value(value, path)


def _is_anywidget(value: Any) -> bool:
    from marimo._plugins.ui._impl.from_anywidget import anywidget

    return isinstance(value, anywidget)


def _is_sensitive(value: Any) -> bool:
    return any(_component_kind(item) == "password" for item in _control_tree(value))


def _component_kind(value: Any) -> object:
    args = getattr(value, "_component_args", None)
    return args.get("kind") if isinstance(args, Mapping) else None


def _control_tree(root: Any) -> list[Any]:
    return [element for element, _path in _control_tree_entries(root)]


def _control_tree_entries(
    root: Any,
) -> list[tuple[Any, tuple[ControlPathStep, ...]]]:
    from marimo._plugins.ui._core.ui_element import UIElement

    pending: list[tuple[Any, tuple[ControlPathStep, ...]]] = [(root, ())]
    result: list[tuple[Any, tuple[ControlPathStep, ...]]] = []
    seen: set[int] = set()
    while pending:
        value, path = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        result.append((value, path))
        children: list[tuple[Any, tuple[ControlPathStep, ...]]] = []
        element = getattr(value, "element", None)
        if isinstance(element, UIElement):
            children.append((element, (*path, ControlElementStep())))
        elements = getattr(value, "elements", None)
        if isinstance(elements, Mapping):
            for key, item in elements.items():
                if not isinstance(key, str):
                    raise ExecutionError("UI control mapping keys must be strings")
                if isinstance(item, UIElement):
                    children.append((item, (*path, ControlKeyStep(value=key))))
        elif isinstance(elements, (list, tuple)):
            children.extend(
                (item, (*path, ControlIndexStep(value=index)))
                for index, item in enumerate(elements)
                if isinstance(item, UIElement)
            )
        pending.extend(reversed(children))
    return result


def _control_domain(value: Any) -> JsonObject:
    if _is_sensitive(value):
        return {}
    args = getattr(value, "_component_args", None)
    if not isinstance(args, Mapping):
        return {}
    result: JsonObject = {}
    for name in ("debounce", "full_width", "max", "min", "options", "step"):
        if name not in args:
            continue
        try:
            result[name] = json_value(args[name], f"control domain {name!r}")
        except (TypeError, ValueError):
            continue
    return result


__all__ = [
    "declared_ui_values",
    "document_sha256_from_source",
    "inspect_baseline",
    "install_parent_stop_provenance",
    "observe_kernel_inputs",
    "require_capabilities",
    "runtime_path",
    "validate_parent_state",
]
