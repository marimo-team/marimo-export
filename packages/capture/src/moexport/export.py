"""Public export orchestration for live marimo notebook sessions."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from moexport.bundle import write_bundle
from moexport.evaluate import EvaluateResult, evaluate
from moexport.request import resolve_export_request
from moexport.spec import ExportSpec, parse_export_spec

SpecInput: TypeAlias = ExportSpec | Mapping[str, Any]


class ExportResult(BaseModel):
    """Summary returned by `moexport.export`."""

    model_config = ConfigDict(frozen=True)

    bundle_path: str = Field(description="Filesystem path to the written bundle.")
    manifest_path: str = Field(
        description="Filesystem path to the bundle manifest JSON file.",
    )
    manifest: dict[str, Any] = Field(
        description="Manifest data written to `manifest.json`.",
    )
    invocation_path: str = Field(
        description="Filesystem path to the invocation trace JSON file.",
    )
    invocation_index_path: str = Field(
        description="Filesystem path to the bundle invocation index JSON file.",
    )
    invocation: dict[str, Any] = Field(
        description="Invocation trace data written for this concrete export run.",
    )
    evaluation: EvaluateResult = Field(
        description="Raw `mox.evaluate` result used to produce the bundle.",
    )


async def export(
    spec: SpecInput,
    *,
    bundle: str | Path | None = None,
) -> ExportResult:
    """Evaluate an export spec and write a static export bundle.

    This must run inside a live marimo notebook session. Scenarios are mapped
    to one batched `evaluate(...)` call so clean cells can be reused across the
    scenario matrix.
    """

    request = await resolve_export_request(
        parse_export_spec(spec),
        bundle=bundle,
        evaluate_fn=evaluate,
    )
    evaluate_kwargs: dict[str, Any] = {
        "object_patches": [scenario.object_patches for scenario in request.scenarios],
    }
    if request.output_cell_ids:
        evaluate_kwargs["output_cell_ids"] = request.output_cell_ids
    if request.output_error_policy != "raise":
        evaluate_kwargs["output_error_policy"] = request.output_error_policy
    evaluation = await evaluate(
        request.target,
        [scenario.definition_overrides for scenario in request.scenarios],
        **evaluate_kwargs,
    )
    written = await write_bundle(request, evaluation)

    return ExportResult(
        bundle_path=str(written.bundle_path),
        manifest_path=str(written.manifest_path),
        manifest=written.manifest,
        invocation_path=str(written.invocation_path),
        invocation_index_path=str(written.invocation_index_path),
        invocation=written.invocation,
        evaluation=evaluation,
    )
