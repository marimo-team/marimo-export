"""Local execution primitives for planned notebook cells.

The evaluator does not ask marimo's reactive runner to recompute cells. Instead
it executes selected cell bodies into an isolated globals dictionary, evaluates
their display output when needed, and builds cache keys for reusing pure cell
results across batched scenario variants.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from marimo._ast.cell import CellImpl
from marimo._runtime.context.types import RuntimeContext

from moexport.evaluate._analysis import body_refs
from moexport.evaluate._types import ObjectPatches
from moexport.evaluate._values import value_fingerprint
from moexport.runtime import materialize_cell_output


def _is_coroutine_code(code: Any) -> bool:
    return code is not None and bool(code.co_flags & inspect.CO_COROUTINE)


async def execute_cell_body(cell: CellImpl, glbls: dict[str, Any]) -> None:
    if cell.body is None:
        return

    if _is_coroutine_code(cell.body):
        await eval(cell.body, glbls)
        return

    exec(cell.body, glbls)


async def evaluate_cell_output(
    cell: CellImpl,
    glbls: dict[str, Any],
    ctx: RuntimeContext,
) -> Any:
    output = materialize_cell_output(ctx, cell.cell_id, values=glbls)
    if inspect.isawaitable(output):
        output = await output

    return output


def cell_cache_key(
    cell: CellImpl,
    glbls: Mapping[str, Any],
    object_patches: ObjectPatches,
) -> tuple[Any, ...]:
    refs = sorted(body_refs(cell))
    ref_set = set(refs)
    relevant_patches = {
        target: value
        for target, value in object_patches.items()
        if target.split(".", maxsplit=1)[0] in ref_set
    }
    return (
        str(cell.cell_id),
        cell.key,
        tuple((ref, value_fingerprint(glbls[ref])) for ref in refs if ref in glbls),
        tuple(
            (target, value_fingerprint(value))
            for target, value in sorted(
                relevant_patches.items(), key=lambda item: item[0]
            )
        ),
    )
