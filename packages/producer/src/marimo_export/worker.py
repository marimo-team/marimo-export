from __future__ import annotations

import time
from typing import Any

from marimo_export import __version__
from marimo_export._builtin_exporters import BUILTIN_EXPORTERS
from marimo_export._marimo.compat import MARIMO_ADAPTER, require_supported_marimo
from marimo_export.errors import InvalidPlanError, ScenarioBuildError
from marimo_export.index import (
    BuildReceipt,
    ExportIndex,
    ExportRef,
    ProducerInfo,
    export_ref,
)
from marimo_export.plan import decode_plan


async def build(plan_value: object) -> tuple[ExportRef, BuildReceipt]:
    marimo_version = require_supported_marimo()
    try:
        plan = decode_plan(plan_value)
    except (TypeError, ValueError) as error:
        raise InvalidPlanError(str(error)) from error

    from marimo_export._marimo.context import require_producer_context

    require_producer_context()

    from marimo_export._marimo.cache import put_index, read_payload
    from marimo_export._marimo.context import assert_snapshot_current, notebook_snapshot
    from marimo_export._marimo.runner import run_scenario_in_child

    snapshot = notebook_snapshot()
    started = time.perf_counter()
    results = []
    for scenario in plan.scenarios:
        try:
            results.append(await run_scenario_in_child(plan, scenario, snapshot))
        except Exception as error:
            raise ScenarioBuildError(scenario.id, error) from error

    index = ExportIndex(
        notebook_name=snapshot.name,
        notebook_source_sha256=snapshot.source_sha256,
        plan_sha256=plan.sha256,
        producer=ProducerInfo(
            marimo_version=marimo_version,
            marimo_export_version=__version__,
        ),
        scenarios=tuple(results),
    )
    for payload in index.payloads():
        read_payload(payload.key, payload.sha256, payload.size)
    assert_snapshot_current(snapshot)
    ref, index_bytes = export_ref(index)
    put_index(ref.key, index_bytes)

    receipt = BuildReceipt(
        elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
        scenario_count=len(results),
        projection_count=sum(
            len(formats) for result in results for formats in result.outputs.values()
        ),
    )
    return ref, receipt


def describe() -> dict[str, Any]:
    marimo_version = require_supported_marimo()
    return {
        "protocol": "marimo-export.remote.v1",
        "marimo_export_version": __version__,
        "marimo_version": marimo_version,
        "adapter": MARIMO_ADAPTER,
        "projections": _projection_capabilities(),
    }


def _projection_capabilities() -> dict[str, dict[str, bool | str | None]]:
    return {
        descriptor.name: {
            "available": descriptor.available(),
            "extra": descriptor.extra,
        }
        for descriptor in BUILTIN_EXPORTERS
    }
