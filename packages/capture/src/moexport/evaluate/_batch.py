"""Batch orchestration for the public ``evaluate(...)`` call.

The public API accepts one definition override/object patch set, or a batch of
sets. This module normalizes those shapes, runs each variant through the
single-target evaluator, and aggregates metadata while sharing one per-call
cell cache across variants.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import cast

from moexport.evaluate._target import evaluate_target_once
from moexport.evaluate._types import (
    CellCache,
    DefinitionOverrides,
    EvaluateResult,
    JsonDict,
    ObjectPatches,
    TargetRunResult,
)


def _normalize_definition_override_sets(
    definition_overrides: DefinitionOverrides | Sequence[DefinitionOverrides] | None,
) -> list[DefinitionOverrides]:
    if definition_overrides is None:
        return [{}]

    if isinstance(definition_overrides, Mapping):
        return [cast(DefinitionOverrides, definition_overrides)]

    if isinstance(definition_overrides, str | bytes | bytearray):
        raise TypeError(
            "definition_overrides must be a mapping, a sequence of mappings, or None"
        )
    if not isinstance(definition_overrides, Sequence):
        raise TypeError(
            "definition_overrides must be a mapping, a sequence of mappings, or None"
        )

    return list(definition_overrides)


def _normalize_object_patch_sets(
    object_patches: ObjectPatches | Sequence[ObjectPatches] | None,
    *,
    count: int,
) -> list[ObjectPatches]:
    if object_patches is None:
        return [{} for _ in range(count)]

    if isinstance(object_patches, Mapping):
        return [cast(ObjectPatches, object_patches) for _ in range(count)]

    if isinstance(object_patches, str | bytes | bytearray):
        raise TypeError(
            "object_patches must be a mapping, a sequence of mappings, or None"
        )
    if not isinstance(object_patches, Sequence):
        raise TypeError(
            "object_patches must be a mapping, a sequence of mappings, or None"
        )

    patch_sets = list(object_patches)
    if len(patch_sets) != count:
        raise ValueError(
            f"expected {count} object patch set(s) to match definition "
            f"override sets; got {len(patch_sets)}"
        )
    return patch_sets


def _aggregate_execution_metadata(
    results: list[TargetRunResult],
    elapsed_ms: float,
) -> JsonDict:
    stats = {
        "required": 0,
        "scheduled": 0,
        "executed": 0,
        "cached": 0,
        "pruned": 0,
        "skipped": 0,
    }

    for result in results:
        result_stats = result["metadata"]["execution"]["stats"]
        for key in stats:
            stats[key] += result_stats[key]

    return {
        "elapsed_ms": elapsed_ms,
        "stats": stats,
    }


async def evaluate(
    target: str,
    definition_overrides: DefinitionOverrides
    | Sequence[DefinitionOverrides]
    | None = None,
    *,
    object_patches: ObjectPatches | Sequence[ObjectPatches] | None = None,
) -> EvaluateResult:
    """Evaluate a target once or over a batch of finite scenario states.

    The output shape is always the same: a target envelope with a ``results``
    list. A single definition override/object patch pair is represented as one
    result. Every call shares a per-call cell cache, so repeated variants can
    reuse produced defs instead of re-executing cells with identical body
    dependencies.
    """
    cell_cache: CellCache = {}
    override_sets = _normalize_definition_override_sets(definition_overrides)
    patch_sets = _normalize_object_patch_sets(
        object_patches,
        count=len(override_sets),
    )
    started = time.perf_counter()
    results = [
        await evaluate_target_once(
            target,
            override_set,
            object_patches=patch_set,
            cell_cache=cell_cache,
        )
        for override_set, patch_set in zip(override_sets, patch_sets, strict=True)
    ]
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    return {
        "target": target,
        "results": results,
        "metadata": {
            "batch": {
                "result_count": len(results),
                "cache_scope": "call",
            },
            "execution": _aggregate_execution_metadata(results, elapsed_ms),
        },
    }
