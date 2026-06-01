"""Shared evaluator types and lightweight records.

The evaluator modules pass a few structured records through the pipeline:
completed definition replacements, target plans, trace payloads, and the active
runtime binding used by expression helpers. Keeping them here avoids circular
imports and keeps cross-module contracts explicit.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeAlias

from marimo._runtime.context.types import RuntimeContext
from marimo._types.ids import CellId_t

from moexport.runtime import materialize_cell_output

TargetKind: TypeAlias = Literal["definition", "expression"]
CellStatus: TypeAlias = Literal[
    "executed",
    "cached",
    "pruned",
    "skipped",
    "needed",
    "inactive",
]
DefinitionOverrides: TypeAlias = Mapping[str, Any]
ObjectPatches: TypeAlias = Mapping[str, Any]
JsonDict: TypeAlias = dict[str, Any]
TargetRunResult: TypeAlias = dict[str, Any]
EvaluateResult: TypeAlias = dict[str, Any]
CellCache: TypeAlias = dict[tuple[Any, ...], dict[str, Any]]
DefaultCellCache: TypeAlias = dict[CellId_t, dict[str, Any]]
AutoFilledOverrides: TypeAlias = dict[str, dict[str, str]]


class OverrideCompletion:
    __slots__ = ("auto_filled", "explicit_names", "values")

    def __init__(
        self,
        *,
        values: dict[str, Any],
        explicit_names: set[str],
        auto_filled: AutoFilledOverrides,
    ) -> None:
        self.values = values
        self.explicit_names = explicit_names
        self.auto_filled = auto_filled


class TargetPlan:
    __slots__ = (
        "dirty_cells",
        "expression_refs",
        "kind",
        "live_values",
        "object_patch_roots",
        "override_refs",
        "root_names",
    )

    def __init__(
        self,
        *,
        kind: TargetKind,
        root_names: list[str],
        expression_refs: list[str],
        dirty_cells: set[CellId_t],
        live_values: dict[str, Any],
        override_refs: set[str],
        object_patch_roots: set[str],
    ) -> None:
        self.kind = kind
        self.root_names = root_names
        self.expression_refs = expression_refs
        self.dirty_cells = dirty_cells
        self.live_values = live_values
        self.override_refs = override_refs
        self.object_patch_roots = object_patch_roots


class TraceMetadata:
    __slots__ = ("execution", "graph")

    def __init__(self, *, graph: JsonDict, execution: JsonDict) -> None:
        self.graph = graph
        self.execution = execution


class ActiveEvaluation:
    __slots__ = ("outputs", "runtime")

    def __init__(self, *, runtime: RuntimeContext, outputs: dict[str, Any]) -> None:
        self.runtime = runtime
        self.outputs = outputs

    def cell_output(self, cell_id: CellId_t) -> Any:
        key = str(cell_id)
        if key in self.outputs:
            return self.outputs[key]
        return materialize_cell_output(self.runtime, cell_id)
