from __future__ import annotations

import copy
import hashlib
import sys
from collections.abc import Awaitable, Callable, Iterable
from types import SimpleNamespace
from typing import Any

from marimo._ast.variables import if_local_then_mangle
from marimo._runtime.app.kernel_runner import AppKernelRunner
from marimo._runtime.dataflow import topological_sort
from marimo._runtime.state import SetFunctor, State
from marimo._types.ids import CellId_t

from marimo_export._json import canonical_bytes
from marimo_export._marimo.cache import flush_caches
from marimo_export._marimo.html import prepare_html_cache_text
from marimo_export.projection.synthetic_cells import SyntheticProjectionCell


async def _run_targeted_cells(runner: AppKernelRunner, cells: set[CellId_t]) -> None:
    flush_caches()
    # Embedded kernels share the process-level sys module. Reinstall the child
    # argv at each execution boundary because another kernel may have replaced
    # it since this runner was configured.
    sys.argv = runner._kernel.argv
    try:
        await runner.run(cells)
    finally:
        flush_caches()


async def run_projection_cells(
    runner: AppKernelRunner,
    graph: Any,
    cells: set[CellId_t],
) -> None:
    for cache_cells, group in _frontier_groups(runner, cells, graph=graph):
        await _materialize_nested_stub_refs(runner, graph, group)
        requested = set(group)
        stub_producers = _stub_producers(runner, graph, group)
        if cache_cells:
            # CachedLifecycle can invalidate an UnhashableStub producer only
            # when its restored key and the consuming miss share one Runner.
            requested.update(stub_producers)
        elif stub_producers:
            await _run_with_cell_cache(runner, stub_producers, enabled=False)
        await _run_with_cell_cache(runner, requested, enabled=cache_cells)


async def run_preparation_cells(
    runner: AppKernelRunner,
    graph: Any,
    cells: set[CellId_t],
) -> None:
    await _materialize_nested_stub_refs(runner, graph, cells)
    producers = _stub_producers(runner, graph, cells)
    if producers:
        await _run_with_cell_cache(runner, producers, enabled=False)
    await _run_with_cell_cache(runner, cells, enabled=False)


async def prepare_projection_cache_tokens(
    runner: AppKernelRunner,
    graph: Any,
    synthetic_ids: dict[SyntheticProjectionCell, CellId_t],
    raise_failures: Callable[[], None],
) -> None:
    from marimo._output.hypertext import Html

    repair_ids: set[CellId_t] = set()
    for synthetic, cell_id in synthetic_ids.items():
        if synthetic.preparation is not None:
            continue
        for ref in _projection_runtime_refs(graph, synthetic, cell_id):
            value = _resolve_ref(runner, graph, ref, cell_id)
            if _requires_html_refresh(value, Html):
                repair_ids.update(graph.get_defining_cells(ref))

    if repair_ids:
        # A restored Html retains its virtual URL but the child registry that
        # owned the bytes has gone away. Run just those producers live so the
        # projection can inline the bytes. The projection cache remains on.
        repair_ids = _html_repair_closure(runner, graph, repair_ids, Html)
        await _run_with_cell_cache(runner, repair_ids, enabled=False)
        raise_failures()

    for synthetic, cell_id in synthetic_ids.items():
        if synthetic.preparation is not None:
            continue
        records: list[dict[str, str]] = []
        for ref in sorted(_projection_runtime_refs(graph, synthetic, cell_id)):
            value = _resolve_ref(runner, graph, ref, cell_id)
            for path, html in _html_values(value, Html):
                value_type = type(html)
                records.append(
                    {
                        "ref": f"{ref}{path}",
                        "type": f"{value_type.__module__}.{value_type.__qualname__}",
                        "text": prepare_html_cache_text(html.text),
                    }
                )
        runner.globals[synthetic.cache_token_name] = hashlib.sha256(
            canonical_bytes(records)
        ).digest()


def _projection_runtime_refs(
    graph: Any,
    synthetic: SyntheticProjectionCell,
    cell_id: CellId_t,
) -> set[str]:
    direct = set(graph.cells[cell_id].refs) - {synthetic.cache_token_name}
    # Expression functions and notebook exporters can close over Html values.
    # Follow the same function-level references that marimo includes in native
    # cache identity so virtual media is repaired before a synthetic miss.
    return graph.get_transitive_references(direct, inclusive=True)


def _stub_producers(
    runner: AppKernelRunner,
    graph: Any,
    cells: set[CellId_t],
) -> set[CellId_t]:
    producers: set[CellId_t] = set()
    pending = set(cells)
    while pending:
        cell_id = pending.pop()
        cell = graph.cells[cell_id]
        for ref in cell.refs:
            value = _resolve_ref(runner, graph, ref, cell_id)
            if getattr(type(value), "__marimo_unhashable__", False) is True:
                discovered = graph.get_defining_cells(ref) - cells - producers
                producers.update(discovered)
                pending.update(discovered)
    return producers - cells


async def _materialize_nested_stub_refs(
    runner: AppKernelRunner,
    graph: Any,
    cells: set[CellId_t],
) -> None:
    if not any(
        _contains_nested_unhashable_stub(_resolve_ref(runner, graph, ref, cell_id))
        for cell_id in cells
        for ref in graph.cells[cell_id].refs
    ):
        return

    materialize: set[CellId_t] = set()
    pending = set(cells)
    visited = set(cells)
    while pending:
        cell_id = pending.pop()
        for ref in graph.cells[cell_id].refs:
            value = _resolve_ref(runner, graph, ref, cell_id)
            if not _contains_unhashable_stub(value):
                continue
            discovered = graph.get_defining_cells(ref) - visited
            visited.update(discovered)
            materialize.update(discovered)
            pending.update(discovered)
    if materialize:
        await _run_with_cell_cache(runner, materialize, enabled=False)


def _contains_unhashable_stub(value: Any) -> bool:
    if getattr(type(value), "__marimo_unhashable__", False) is True:
        return True
    return _contains_nested_unhashable_stub(value)


def _contains_nested_unhashable_stub(value: Any) -> bool:
    return any(
        getattr(type(item), "__marimo_unhashable__", False) is True
        for item in _nested_runtime_values(value)
    )


def _html_repair_closure(
    runner: AppKernelRunner,
    graph: Any,
    cells: set[CellId_t],
    html_type: type,
) -> set[CellId_t]:
    closure = set(cells)
    pending = set(cells)
    while pending:
        cell_id = pending.pop()
        cell = graph.cells[cell_id]
        for ref in cell.refs:
            value = _resolve_ref(runner, graph, ref, cell_id)
            is_stub = getattr(type(value), "__marimo_unhashable__", False) is True
            if not is_stub and not _requires_html_refresh(value, html_type):
                continue
            discovered = graph.get_defining_cells(ref) - closure
            closure.update(discovered)
            pending.update(discovered)
    return closure


def _requires_html_refresh(value: Any, html_type: type) -> bool:
    for _, html in _html_values(value, html_type):
        try:
            prepare_html_cache_text(html.text)
        except ValueError:
            return True
    return False


def _html_values(
    value: Any,
    html_type: type,
    *,
    path: str = "",
    seen: set[int] | None = None,
) -> Iterable[tuple[str, Any]]:
    if isinstance(value, html_type):
        yield path, value
        return
    if not isinstance(value, (dict, list, tuple)):
        return

    if seen is None:
        seen = set()
    identity = id(value)
    if identity in seen:
        return
    seen.add(identity)
    if isinstance(value, dict):
        items = ((f"[{index}:{key!r}]", item) for index, (key, item) in enumerate(value.items()))
    else:
        items = ((f"[{index}]", item) for index, item in enumerate(value))
    for suffix, item in items:
        yield from _html_values(
            item,
            html_type,
            path=f"{path}{suffix}",
            seen=seen,
        )


async def run_to_quiescence(
    runner: AppKernelRunner,
    initial: set[CellId_t],
    allowed: set[CellId_t],
    after_frontier: Callable[[], Awaitable[None]] | None = None,
) -> None:
    pending = initial & allowed
    rounds = 0
    max_rounds = max(32, len(allowed) * 4)
    while pending:
        rounds += 1
        if rounds > max_rounds:
            raise RuntimeError("marimo authored graph did not reach quiescence")
        graph = runner._kernel.graph
        roots = {cell_id for cell_id in pending if not (graph.ancestors(cell_id) & pending)}
        if not roots:
            raise RuntimeError("marimo authored graph did not reach quiescence")
        stale: set[CellId_t] = set()
        for cache_cells, group in _frontier_groups(runner, roots):
            stale.difference_update(group)
            await _materialize_nested_stub_refs(runner, graph, group)
            requested = set(group)
            stub_producers = _stub_producers(runner, graph, group) & allowed
            if cache_cells:
                requested.update(stub_producers)
            elif stub_producers:
                await _run_with_cell_cache(runner, stub_producers, enabled=False)
            await _run_with_cell_cache(runner, requested, enabled=cache_cells)
            stale |= runner._kernel.graph.get_stale() & allowed
            await _repair_orphaned_state_defs(runner, group)
            stale |= runner._kernel.graph.get_stale() & allowed
        if after_frontier is not None:
            await after_frontier()
            stale |= runner._kernel.graph.get_stale() & allowed
        pending = (pending - roots) | stale


def _frontier_groups(
    runner: AppKernelRunner,
    cells: set[CellId_t],
    *,
    graph: Any | None = None,
) -> list[tuple[bool, set[CellId_t]]]:
    if graph is None:
        graph = runner._kernel.graph
    groups: list[tuple[bool, set[CellId_t]]] = []
    for cell_id in topological_sort(graph, cells):
        cache_cells = not _requires_live_state_execution(runner, graph, cell_id)
        if groups and groups[-1][0] == cache_cells:
            groups[-1][1].add(cell_id)
        else:
            groups.append((cache_cells, {cell_id}))
    return groups


def _requires_live_state_execution(
    runner: AppKernelRunner,
    graph: Any,
    cell_id: CellId_t,
) -> bool:
    cell = graph.cells[cell_id]
    if "sys" in cell.imported_namespaces:
        return True
    direct_values = _unique_refs(_resolve_ref(runner, graph, name, cell_id) for name in cell.refs)
    nested_direct_values = _unique_refs(
        item for value in direct_values for item in _nested_runtime_values(value)
    )
    if any(value is sys for value in [*direct_values, *nested_direct_values]):
        return True
    if any(isinstance(value, (SetFunctor, State)) for value in nested_direct_values):
        return True
    direct_setters = [value for value in direct_values if isinstance(value, SetFunctor)]
    direct_states = [value for value in direct_values if isinstance(value, State)]

    # CachedLifecycle replays post-state only for direct SetFunctor refs. A
    # transitive setter can affect the key, but a hit cannot replay its write.
    transitive_refs = graph.get_transitive_references(
        set(cell.refs),
        inclusive=True,
    )
    transitive_values = _unique_refs(
        _resolve_ref(runner, graph, name, cell_id) for name in transitive_refs
    )
    nested_transitive_values = _unique_refs(
        item for value in transitive_values for item in _nested_runtime_values(value)
    )
    if any(isinstance(value, SetFunctor) for value in nested_transitive_values):
        return True
    direct_setter_ids = {id(setter) for setter in direct_setters}
    if any(
        isinstance(value, SetFunctor) and id(value) not in direct_setter_ids
        for value in transitive_values
    ):
        return True

    return any(
        all(setter._state is not state for state in direct_states) for setter in direct_setters
    )


def _unique_refs(values: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[int] = set()
    for value in values:
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(value)
    return result


def _nested_runtime_values(
    value: Any,
    *,
    seen: set[int] | None = None,
) -> Iterable[Any]:
    if not isinstance(value, (dict, list, tuple, SimpleNamespace)):
        return
    if seen is None:
        seen = set()
    identity = id(value)
    if identity in seen:
        return
    seen.add(identity)
    if isinstance(value, dict):
        children = value.values()
    elif isinstance(value, SimpleNamespace):
        children = vars(value).values()
    else:
        children = value
    for child in children:
        yield child
        yield from _nested_runtime_values(child, seen=seen)


def _resolve_ref(
    runner: AppKernelRunner,
    graph: Any,
    name: str,
    cell_id: CellId_t,
) -> Any:
    if name in runner.globals:
        return runner.globals[name]
    local_name = if_local_then_mangle(name, cell_id)
    if local_name in runner.globals:
        return runner.globals[local_name]
    for defining_cell in graph.get_defining_cells(name):
        lookup = if_local_then_mangle(name, defining_cell)
        if lookup in runner.globals:
            return runner.globals[lookup]
    return None


async def _run_with_cell_cache(
    runner: AppKernelRunner,
    cells: set[CellId_t],
    *,
    enabled: bool,
) -> None:
    if enabled:
        await _run_targeted_cells(runner, cells)
        return

    original_config = runner._kernel.user_config
    uncached_config: Any = copy.deepcopy(original_config)
    uncached_config.setdefault("runtime", {})["cache_cells"] = False
    runner._kernel.user_config = uncached_config
    try:
        await _run_targeted_cells(runner, cells)
    finally:
        runner._kernel.user_config = original_config


async def _repair_orphaned_state_defs(
    runner: AppKernelRunner,
    cells: set[CellId_t],
) -> None:
    repair_ids = _orphaned_state_cells(runner, cells)
    if not repair_ids:
        return

    await _run_with_cell_cache(runner, repair_ids, enabled=False)

    remaining = _orphaned_state_cells(runner, repair_ids)
    if remaining:
        cell_ids = ", ".join(sorted(str(cell_id) for cell_id in remaining))
        raise RuntimeError(f"marimo state definitions remained detached in cells: {cell_ids}")


def _orphaned_state_cells(
    runner: AppKernelRunner,
    cells: set[CellId_t],
) -> set[CellId_t]:
    result: set[CellId_t] = set()
    graph = runner._kernel.graph
    for cell_id in cells:
        cell = graph.cells[cell_id]
        direct_values = [
            runner.globals.get(if_local_then_mangle(name, cell_id)) for name in cell.defs
        ]
        nested_values = [item for value in direct_values for item in _nested_runtime_values(value)]
        states = [value for value in [*direct_values, *nested_values] if isinstance(value, State)]
        setters = [
            value for value in [*direct_values, *nested_values] if isinstance(value, SetFunctor)
        ]
        detached_setter = any(
            all(setter._state is not state for state in states) for setter in setters
        )
        detached_state = any(
            all(state._set_value is not setter for setter in setters) for state in states
        )
        if states and setters and (detached_setter or detached_state):
            result.add(cell_id)
    return result
