from __future__ import annotations

import ast
import keyword
from dataclasses import dataclass
from typing import Literal, cast

from marimo_export._builtin_exporters import (
    builtin_exporter,
    normalize_builtin_options,
)
from marimo_export._json import (
    JsonObject,
    JsonValue,
    json_identity,
    json_object,
    json_value,
    sha256_json,
)

PLAN_SCHEMA = "marimo-export.plan.v1"


@dataclass(frozen=True)
class InputBinding:
    kind: Literal["definition", "ui"]
    target: str

    def wire(self) -> JsonObject:
        return {self.kind: self.target}


@dataclass(frozen=True)
class InputPlan:
    name: str
    binding: InputBinding
    default: JsonValue
    has_default: bool

    def wire(self) -> JsonObject:
        result = self.binding.wire()
        if self.has_default:
            result["default"] = self.default
        return result


@dataclass(frozen=True)
class Source:
    kind: Literal["definition", "expression"]
    value: str

    def wire(self) -> JsonValue:
        if self.kind == "definition":
            return self.value
        return {"expression": self.value}


@dataclass(frozen=True)
class ExporterSpec:
    kind: Literal["ref", "definition"]
    source: str
    version: str | None

    def wire(self) -> JsonObject:
        result: JsonObject = {self.kind: self.source}
        if self.version is not None:
            result["version"] = self.version
        return result


@dataclass(frozen=True)
class FormatPlan:
    name: str
    exporter: ExporterSpec
    options: JsonObject

    def wire(self) -> JsonObject:
        return {"exporter": self.exporter.wire(), "options": self.options}


@dataclass(frozen=True)
class OutputPlan:
    name: str
    source: Source
    formats: tuple[FormatPlan, ...]

    def wire(self) -> JsonObject:
        return {
            "source": self.source.wire(),
            "formats": {item.name: item.wire() for item in self.formats},
        }


@dataclass(frozen=True)
class Scenario:
    id: str
    inputs: JsonObject

    def wire(self) -> JsonObject:
        return {"id": self.id, "inputs": self.inputs}


@dataclass(frozen=True)
class ExportPlan:
    inputs: tuple[InputPlan, ...]
    scenarios: tuple[Scenario, ...]
    outputs: tuple[OutputPlan, ...]

    def wire(self) -> JsonObject:
        return {
            "schema": PLAN_SCHEMA,
            "inputs": {item.name: item.wire() for item in self.inputs},
            "scenarios": [item.wire() for item in self.scenarios],
            "outputs": {item.name: item.wire() for item in self.outputs},
        }

    @property
    def sha256(self) -> str:
        return sha256_json(self.wire())


def decode_plan(value: object) -> ExportPlan:
    root = _mapping(value, "plan")
    _keys(root, {"schema", "inputs", "scenarios", "outputs"}, "plan")
    if "schema" not in root:
        raise ValueError("plan.schema is required")
    schema = root.get("schema")
    if schema != PLAN_SCHEMA:
        raise ValueError(f"plan.schema must be {PLAN_SCHEMA!r}")

    inputs_value = _mapping(root.get("inputs", {}), "plan.inputs")
    inputs = tuple(_input(name, item) for name, item in inputs_value.items())
    targets = [item.binding.target for item in inputs]
    if len(targets) != len(set(targets)):
        raise ValueError("plan.inputs bindings must be unique")

    scenarios_value = root.get("scenarios", [{"id": "default", "inputs": {}}])
    if not isinstance(scenarios_value, list) or not scenarios_value:
        raise TypeError("plan.scenarios must be a non-empty array")
    scenarios = tuple(_scenario(item, index, inputs) for index, item in enumerate(scenarios_value))
    ids = [item.id for item in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("plan.scenarios ids must be unique")
    vectors = [json_identity(item.inputs) for item in scenarios]
    if len(vectors) != len(set(vectors)):
        raise ValueError("plan.scenarios must resolve to unique input vectors")

    outputs_value = _mapping(root.get("outputs"), "plan.outputs")
    if not outputs_value:
        raise ValueError("plan.outputs must contain at least one output")
    outputs = tuple(_output(name, item) for name, item in outputs_value.items())
    return ExportPlan(inputs=inputs, scenarios=scenarios, outputs=outputs)


def _input(name: object, value: object) -> InputPlan:
    input_name = _name(name, "plan.inputs key")
    path = f"plan.inputs.{input_name}"
    item = _mapping(value, path)
    _keys(item, {"definition", "ui", "default"}, path)
    binding_keys = set(item) & {"definition", "ui"}
    if len(binding_keys) != 1:
        raise ValueError(f"{path} must select exactly one of definition or ui")
    kind = binding_keys.pop()
    binding = InputBinding(
        kind=cast(Literal["definition", "ui"], kind),
        target=_definition(item[kind], f"{path}.{kind}"),
    )
    has_default = "default" in item
    default = json_value(item.get("default"), f"{path}.default")
    return InputPlan(input_name, binding, default, has_default)


def _scenario(value: object, index: int, inputs: tuple[InputPlan, ...]) -> Scenario:
    path = f"plan.scenarios[{index}]"
    item = _mapping(value, path)
    _keys(item, {"id", "inputs"}, path)
    scenario_id = _name(item.get("id"), f"{path}.id")
    provided = json_object(item.get("inputs", {}), f"{path}.inputs")
    known = {input_plan.name for input_plan in inputs}
    unknown = set(provided) - known
    if unknown:
        raise ValueError(f"{path}.inputs does not accept: {', '.join(sorted(unknown))}")
    resolved: JsonObject = {}
    missing: list[str] = []
    for input_plan in inputs:
        if input_plan.name in provided:
            resolved[input_plan.name] = provided[input_plan.name]
        elif input_plan.has_default:
            resolved[input_plan.name] = input_plan.default
        else:
            missing.append(input_plan.name)
    if missing:
        raise ValueError(f"{path}.inputs is missing: {', '.join(missing)}")
    return Scenario(id=scenario_id, inputs=resolved)


def _output(name: object, value: object) -> OutputPlan:
    output_name = _name(name, "plan.outputs key")
    path = f"plan.outputs.{output_name}"
    item = _mapping(value, path)
    _keys(item, {"source", "formats"}, path)
    source = _source(item.get("source"), f"{path}.source")
    formats_value = _mapping(item.get("formats"), f"{path}.formats")
    if not formats_value:
        raise ValueError(f"{path}.formats must contain at least one format")
    formats = tuple(_format(name, spec, path) for name, spec in formats_value.items())
    return OutputPlan(name=output_name, source=source, formats=formats)


def _source(value: object, path: str) -> Source:
    if isinstance(value, str):
        return Source(kind="definition", value=_definition(value, path))
    item = _mapping(value, path)
    if set(item) != {"expression"}:
        raise ValueError(f"{path} must be a definition string or an expression object")
    expression = _name(item["expression"], f"{path}.expression")
    try:
        ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise ValueError(f"{path}.expression must be a valid Python expression") from error
    return Source(kind="expression", value=expression)


def _format(name: object, value: object, parent: str) -> FormatPlan:
    format_name = _name(name, f"{parent}.formats key")
    path = f"{parent}.formats.{format_name}"
    item = _mapping(value, path)
    _keys(item, {"exporter", "options"}, path)
    exporter_value = item.get("exporter", format_name)
    exporter = _exporter(exporter_value, f"{path}.exporter")
    options = json_object(item.get("options", {}), f"{path}.options")
    if isinstance(exporter_value, str):
        options = normalize_builtin_options(exporter_value, options, f"{path}.options")
    return FormatPlan(
        name=format_name,
        exporter=exporter,
        options=options,
    )


def _exporter(value: object, path: str) -> ExporterSpec:
    if isinstance(value, str):
        try:
            descriptor = builtin_exporter(value)
        except ValueError as error:
            raise ValueError(
                f"{path} must name a built-in exporter or an exporter object"
            ) from error
        return ExporterSpec(
            kind="ref",
            source=descriptor.ref,
            version=descriptor.cache_version,
        )
    item = _mapping(value, path)
    keys = set(item)
    if keys not in (
        {"ref", "version"},
        {"definition"},
        {"definition", "version"},
    ):
        raise ValueError(f"{path} must contain ref plus version, or a notebook definition")
    kind = "ref" if "ref" in item else "definition"
    source = _name(item.get(kind), f"{path}.{kind}")
    version = _name(item.get("version"), f"{path}.version") if "version" in item else None
    if kind == "ref":
        if version is None:
            raise ValueError(f"{path}.version is required for an importable exporter")
        if source.count(":") != 1:
            raise ValueError(f"{path}.ref must use module:object syntax")
        module, object_name = source.split(":")
        _dotted_identifier(module, f"{path}.ref module")
        _dotted_identifier(object_name, f"{path}.ref object")
    else:
        source = _definition(source, f"{path}.definition")
    return ExporterSpec(
        kind=kind,
        source=source,
        version=version,
    )


def _mapping(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TypeError(f"{path} must be an object with string keys")
    return cast(dict[str, object], value)


def _name(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{path} must be a non-empty string")
    return value


def _definition(value: object, path: str) -> str:
    name = _name(value, path)
    if not name.isidentifier() or keyword.iskeyword(name):
        raise ValueError(f"{path} must be a Python identifier")
    return name


def _dotted_identifier(value: str, path: str) -> None:
    parts = value.split(".")
    if any(not part.isidentifier() or keyword.iskeyword(part) for part in parts):
        raise ValueError(f"{path} must contain dotted Python identifiers")


def _keys(value: dict[str, object], allowed: set[str], path: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{path} does not accept: {', '.join(sorted(unknown))}")
