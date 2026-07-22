from __future__ import annotations

import hashlib
import importlib
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from marimo_export import Projection
from marimo_export._builtin_exporters import builtin_exporter
from marimo_export._json import JsonObject, canonical_bytes, json_object
from marimo_export.plan import ExporterSpec, FormatPlan, Source

PROJECTION_CELL_ABI = 2


@dataclass(frozen=True)
class SyntheticPreparationCell:
    result_name: str
    code: str


@dataclass(frozen=True)
class SyntheticProjectionCell:
    result_name: str
    cache_token_name: str
    code: str
    preparation: SyntheticPreparationCell | None


@dataclass(frozen=True)
class ProjectionBinding:
    output_name: str
    format_name: str
    cell: SyntheticProjectionCell


def projection_binding(
    *,
    output_name: str,
    format_name: str,
    source: Source,
    format_plan: FormatPlan,
    _cache_token_name: str | None = None,
) -> ProjectionBinding:
    identity = {
        "projection_cell_abi": PROJECTION_CELL_ABI,
        "source": source.wire(),
        "exporter": format_plan.exporter.wire(),
        "options": format_plan.options,
    }
    token = hashlib.sha256(canonical_bytes(identity)).hexdigest()[:20]
    result_name = f"marimo_export_projection_{token}"
    cache_token_name = _cache_token_name or f"{result_name}_cache_token"
    expression = _source_expression(source)

    exporter_json = json.dumps(
        format_plan.exporter.wire(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    options_json = json.dumps(
        format_plan.options,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    anywidget_ref = builtin_exporter("anywidget").ref
    is_anywidget = (
        format_plan.exporter.kind == "ref" and format_plan.exporter.source == anywidget_ref
    )
    preparation: SyntheticPreparationCell | None = None
    if is_anywidget:
        preparation = SyntheticPreparationCell(
            result_name=cache_token_name,
            code=(
                f"{cache_token_name} = __import__(\n"
                "    'marimo_export.projection.synthetic_cells', "
                "fromlist=['prepare_anywidget']\n"
                ").prepare_anywidget(\n"
                f"    {expression},\n"
                ")\n"
                f"{cache_token_name}\n"
            ),
        )
        code = (
            "__import__(\n"
            "    'marimo_export.projection.synthetic_cells', "
            "fromlist=['project_prepared_anywidget']\n"
            ").project_prepared_anywidget(\n"
            f"    {cache_token_name},\n"
            f"    exporter_spec={exporter_json!r},\n"
            f"    options_json={options_json!r},\n"
            f"    projection_cell_abi={PROJECTION_CELL_ABI},\n"
            ")\n"
        )
    else:
        exporter_argument = _exporter_argument(format_plan.exporter)
        code = (
            "await __import__(\n"
            "    'marimo_export.projection.synthetic_cells', fromlist=['project']\n"
            ").project(\n"
            f"    {expression},\n"
            f"    exporter_spec={exporter_json!r},\n"
            f"    options_json={options_json!r}"
            f"{exporter_argument}"
            f",\n    cache_token={cache_token_name}"
            f",\n    projection_cell_abi={PROJECTION_CELL_ABI}"
            ",\n"
            ")\n"
        )
    return ProjectionBinding(
        output_name=output_name,
        format_name=format_name,
        cell=SyntheticProjectionCell(
            result_name=result_name,
            cache_token_name=cache_token_name,
            code=code,
            preparation=preparation,
        ),
    )


def prepare_anywidget(value: Any) -> bytes:
    from marimo_export._marimo.anywidget import anywidget_payload

    return anywidget_payload(value).payload


def project_prepared_anywidget(
    payload: bytes,
    *,
    exporter_spec: str,
    options_json: str,
    projection_cell_abi: int,
) -> Projection:
    if projection_cell_abi != PROJECTION_CELL_ABI:
        raise RuntimeError("projection cell uses an unsupported cache ABI")
    if not isinstance(payload, bytes):
        raise TypeError("prepared AnyWidget payload must be bytes")

    spec = json_object(json.loads(exporter_spec), "exporter")
    descriptor = builtin_exporter("anywidget")
    if spec != {"ref": descriptor.ref, "version": descriptor.cache_version}:
        raise ValueError("prepared AnyWidget projection requires the built-in exporter")
    options = json_object(json.loads(options_json), "options")
    descriptor.normalize_options(options, "anywidget options")

    from marimo_export.projection.exporters.anywidget import _from_payload

    return _from_payload(payload)


async def project(
    value: Any,
    *,
    exporter_spec: str,
    options_json: str,
    cache_token: bytes,
    projection_cell_abi: int,
    exporter: Callable[..., Any] | None = None,
) -> Projection:
    if projection_cell_abi != PROJECTION_CELL_ABI:
        raise RuntimeError("projection cell uses an unsupported cache ABI")
    if not isinstance(cache_token, bytes):
        raise TypeError("projection cache token must be bytes")
    spec = json_object(json.loads(exporter_spec), "exporter")
    options = json_object(json.loads(options_json), "options")
    if exporter is None:
        exporter = _load_ref(spec)
    elif spec.get("definition") is None:
        raise ValueError("a notebook exporter must use a definition spec")
    if not callable(exporter):
        raise TypeError("exporter must be callable")
    result = exporter(value, **options)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, Projection):
        raise TypeError("exporter must return Projection")
    return result


def _source_expression(source: Source) -> str:
    if source.kind == "definition":
        return str(source.value)
    if source.kind == "expression":
        return f"({source.value})"
    raise ValueError("projection sources must use a definition or expression")


def _exporter_argument(exporter: ExporterSpec) -> str:
    if exporter.kind == "definition":
        return f",\n    exporter={exporter.source}"
    return ""


def _load_ref(value: JsonObject) -> Callable[..., Any]:
    version = value.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("exporter version must be a non-empty string")
    ref = value.get("ref")
    if not isinstance(ref, str):
        raise TypeError("exporter ref must be a string")
    module_name, separator, object_name = ref.partition(":")
    if not separator or not module_name or not object_name:
        raise ValueError("exporter ref must use module:object syntax")
    result: Any = importlib.import_module(module_name)
    for part in object_name.split("."):
        result = getattr(result, part)
    if not callable(result):
        raise TypeError(f"exporter {ref!r} is not callable")
    return result
