"""Materialize evaluated notebook values into bundle format records."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Mapping
from types import ModuleType
from typing import Any, Awaitable, Callable, TypeAlias, cast

from pydantic import ValidationError

from moexport.artifacts import Artifact
from moexport.blobs import ContentAddressedBlobStore
from moexport.bundle.records import trace_record
from moexport.evaluate import EvaluateResult
from moexport.exporters._core import BundleExporterContext, Exporter
from moexport.request import ResolvedExportRequest, ResolvedScenario
from moexport.spec import CodeExport, ExportCallable, FormatSpec, RefExport, ValueSpec

ResolvedExporter: TypeAlias = Callable[..., Artifact | Awaitable[Artifact]]


class MaterializedScenarios:
    """Manifest scenarios and invocation traces derived from evaluation results."""

    __slots__ = ("manifest", "traces")

    def __init__(
        self,
        *,
        manifest: list[dict[str, Any]],
        traces: list[dict[str, Any]],
    ) -> None:
        self.manifest = manifest
        self.traces = traces


async def materialize_scenarios(
    *,
    request: ResolvedExportRequest,
    blob_store: ContentAddressedBlobStore,
    evaluation: EvaluateResult,
) -> MaterializedScenarios:
    manifest_scenarios = []
    trace_scenarios = []

    for scenario, result in zip(
        request.scenarios,
        evaluation["results"],
        strict=True,
    ):
        values = result["value"]
        if not isinstance(values, Mapping):
            raise TypeError("export target did not evaluate to a mapping of values")

        manifest_record = {
            "id": scenario.id,
            "state": scenario.manifest_state,
            "values": await materialize_values(
                request=request,
                scenario=scenario,
                values=values,
                blob_store=blob_store,
            ),
        }
        trace_record_ = {
            "id": scenario.id,
            "state": scenario.manifest_state,
            "trace": trace_record(result),
        }
        if scenario.declared_state is not None:
            manifest_record["declared_state"] = scenario.declared_state
            trace_record_["declared_state"] = scenario.declared_state

        manifest_scenarios.append(manifest_record)
        trace_scenarios.append(trace_record_)

    return MaterializedScenarios(
        manifest=manifest_scenarios,
        traces=trace_scenarios,
    )


async def materialize_values(
    *,
    request: ResolvedExportRequest,
    scenario: ResolvedScenario,
    values: Mapping[str, Any],
    blob_store: ContentAddressedBlobStore,
) -> dict[str, dict[str, dict[str, Any]]]:
    scenario_values: dict[str, dict[str, dict[str, Any]]] = {}

    for value_name, value_spec in request.spec.values.items():
        if value_name not in values:
            raise KeyError(f"export target did not produce value {value_name!r}")

        scenario_values[value_name] = await materialize_formats(
            scenario=scenario,
            value=values[value_name],
            value_name=value_name,
            value_spec=value_spec,
            blob_store=blob_store,
        )

    return scenario_values


async def materialize_formats(
    *,
    scenario: ResolvedScenario,
    value: Any,
    value_name: str,
    value_spec: ValueSpec,
    blob_store: ContentAddressedBlobStore,
) -> dict[str, dict[str, Any]]:
    format_records: dict[str, dict[str, Any]] = {}

    for format_name, format_spec in value_spec.formats.items():
        artifact = await export_artifact(
            value=value,
            scenario=scenario,
            value_name=value_name,
            format_name=format_name,
            format_spec=format_spec,
            blob_store=blob_store,
        )
        format_records[format_name] = artifact_record(artifact)

    return format_records


async def export_artifact(
    *,
    value: Any,
    scenario: ResolvedScenario,
    value_name: str,
    format_name: str,
    format_spec: FormatSpec,
    blob_store: ContentAddressedBlobStore,
) -> Artifact:
    exporter = resolve_exporter(format_spec.export)
    ctx = BundleExporterContext(
        scenario_id=scenario.id,
        value_name=value_name,
        format_name=format_name,
        blob_store=blob_store,
    )
    artifact = exporter(value, ctx, **format_spec.options)
    if inspect.isawaitable(artifact):
        artifact = await artifact

    return require_blob_artifact(artifact)


def resolve_exporter(export_spec: ExportCallable) -> ResolvedExporter:
    if isinstance(export_spec, RefExport):
        return resolve_ref_exporter(export_spec.ref)

    if isinstance(export_spec, CodeExport):
        return resolve_code_exporter(export_spec.code)


def resolve_ref_exporter(ref: str) -> ResolvedExporter:
    module_name, _, object_path = ref.partition(":")
    module = importlib.import_module(module_name)
    value: Any = module
    for part in object_path.split("."):
        value = getattr(value, part)

    if not callable(value):
        raise TypeError(f"export ref {ref!r} did not resolve to a callable")

    return cast(ResolvedExporter, value)


def resolve_code_exporter(code: str) -> ResolvedExporter:
    module = ModuleType("__moexport_inline_export__")
    exec(code, module.__dict__)
    value = module.__dict__.get("export")
    if not callable(value):
        raise TypeError("inline export code must define a callable named 'export'")

    return cast(Exporter, value)


def require_blob_artifact(artifact: object) -> Artifact:
    if isinstance(artifact, Mapping):
        artifact_mapping = cast(Mapping[str, Any], artifact)
        data = artifact_mapping.get("data")
        if isinstance(data, Mapping) and data.get("type") != "bundle":
            raise TypeError("exporters must return artifact data with type='bundle'")

    try:
        artifact_model = Artifact.model_validate(artifact)
    except ValidationError as exc:
        raise TypeError("exporters must return a valid Artifact") from exc

    if artifact_model.data.type != "bundle":
        raise TypeError("exporters must return artifact data with type='bundle'")

    if not artifact_model.data.files:
        raise TypeError("blob-backed artifact data must include at least one file")

    return artifact_model


def artifact_record(artifact: Artifact) -> dict[str, Any]:
    return {
        "format_id": artifact.format_id,
        "media_type": artifact.media_type,
        "data": artifact.data.model_dump(mode="json"),
        "metadata": artifact.metadata,
    }
