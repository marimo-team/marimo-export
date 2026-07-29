from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from marimo_export._json import (
    JsonObject,
    JsonValue,
    canonical_bytes,
    json_object,
    json_value,
    sha256_bytes,
)
from marimo_export.errors import SpecError
from marimo_export.exporters._definitions import runtime_reference
from marimo_export.exporters._spec import ExporterSpec, FrozenOption
from marimo_export.spec import ExportSpec


@dataclass(frozen=True, slots=True)
class Definition:
    """One live notebook definition used by matrix planning."""

    name: str
    cell_id: str
    siblings: tuple[str, ...]
    kind: Literal["ordinary", "ui"]
    python_type: str
    value: object
    frontend_value: JsonValue | None = None
    sensitive: bool = False
    domain: Mapping[str, JsonValue] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.isidentifier():
            raise TypeError("definition name must be a Python identifier")
        if not isinstance(self.cell_id, str) or not self.cell_id:
            raise TypeError("definition cell_id must be a non-empty string")
        if (
            not isinstance(self.siblings, tuple)
            or self.name not in self.siblings
            or len(self.siblings) != len(set(self.siblings))
        ):
            raise ValueError("definition siblings must be a unique tuple containing the name")
        if self.kind not in {"ordinary", "ui"}:
            raise ValueError("definition kind must be ordinary or ui")
        if not isinstance(self.python_type, str) or not self.python_type:
            raise TypeError("definition python_type must be a non-empty string")
        if not isinstance(self.sensitive, bool):
            raise TypeError("definition sensitive must be a boolean")
        object.__setattr__(
            self,
            "domain",
            MappingProxyType(json_object(self.domain, "definition domain")),
        )


@dataclass(frozen=True, slots=True)
class Baseline:
    """Live values and graph ownership captured before matrix execution."""

    definitions: Mapping[str, Definition]
    document_sha256: str
    filename: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.definitions, Mapping):
            raise TypeError("baseline definitions must be a mapping")
        parsed: dict[str, Definition] = {}
        for name, definition in self.definitions.items():
            if not isinstance(definition, Definition) or name != definition.name:
                raise TypeError("baseline definitions must map names to matching Definition values")
            parsed[name] = definition
        if (
            not isinstance(self.document_sha256, str)
            or len(self.document_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.document_sha256)
        ):
            raise ValueError("baseline document_sha256 must be a lowercase SHA-256 digest")
        if self.filename is not None and not isinstance(self.filename, str):
            raise TypeError("baseline filename must be a string or None")
        object.__setattr__(self, "definitions", MappingProxyType(parsed))


@dataclass(frozen=True, slots=True)
class NormalizedState:
    """One complete public vector plus native child execution packets."""

    name: str
    inputs: Mapping[str, JsonValue]
    fingerprint: str
    ordinary_overrides: Mapping[str, object]
    ui_values: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "inputs",
            MappingProxyType(json_object(self.inputs, f"state {self.name!r} inputs")),
        )
        object.__setattr__(
            self,
            "ordinary_overrides",
            MappingProxyType(dict(self.ordinary_overrides)),
        )
        object.__setattr__(
            self,
            "ui_values",
            MappingProxyType(json_object(self.ui_values, f"state {self.name!r} UI values")),
        )


@dataclass(frozen=True, slots=True)
class OutputProjection:
    """One public output and its transient representation."""

    name: str
    source: str
    exporter: ExporterSpec | None


@dataclass(frozen=True, slots=True)
class MatrixPlan:
    """Fully normalized matrix ready for marimo-owned execution."""

    states: tuple[NormalizedState, ...]
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    projections: Mapping[str, OutputProjection]
    state_name: str
    state_code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "projections", MappingProxyType(dict(self.projections)))


def normalize_matrix(spec: ExportSpec, baseline: Baseline) -> MatrixPlan:
    """Complete sparse rows and form marimo definition override packets."""

    if not isinstance(spec, ExportSpec):
        raise TypeError("spec must be an ExportSpec")
    if not isinstance(baseline, Baseline):
        raise TypeError("baseline must be a Baseline")

    required = set(spec.inputs)
    required.update(output.source for output in spec.outputs.values())
    missing = sorted(required - set(baseline.definitions))
    if missing:
        raise SpecError(
            f"notebook definitions are unavailable: {', '.join(missing)}",
            code="spec_definition_missing",
            details={"definitions": missing},
        )

    baseline_inputs: JsonObject = {}
    for name in spec.inputs:
        definition = baseline.definitions[name]
        if definition.kind == "ui":
            if definition.sensitive:
                raise SpecError(
                    f"UI input {name!r} is sensitive",
                    code="spec_input_sensitive",
                    details={"input": name},
                )
            value = definition.frontend_value
        else:
            value = definition.value
        try:
            baseline_inputs[name] = json_value(value, f"baseline input {name!r}")
        except (TypeError, ValueError) as error:
            raise SpecError(
                f"baseline input {name!r} is not portable",
                code="spec_input_invalid",
                details={"input": name, "python_type": definition.python_type},
            ) from error

    wire_states = spec.to_value()["states"]
    assert isinstance(wire_states, dict)
    fingerprints: dict[bytes, str] = {}
    normalized: list[NormalizedState] = []
    for state_name in sorted(spec.states):
        authored = wire_states[state_name]
        assert isinstance(authored, dict)
        inputs = dict(baseline_inputs)
        inputs.update(authored)
        vector = canonical_bytes(inputs)
        previous = fingerprints.setdefault(vector, state_name)
        if previous != state_name:
            raise SpecError(
                f"states {previous!r} and {state_name!r} normalize to the same input vector",
                code="spec_state_duplicate",
                details={"states": [previous, state_name]},
            )

        ordinary = _ordinary_packet(spec, baseline, inputs)
        ui_values = {
            name: inputs[name] for name in spec.inputs if baseline.definitions[name].kind == "ui"
        }
        normalized.append(
            NormalizedState(
                name=state_name,
                inputs=inputs,
                fingerprint=sha256_bytes(vector),
                ordinary_overrides=ordinary,
                ui_values=ui_values,
            )
        )

    projections = {
        name: OutputProjection(
            name=name,
            source=output.source,
            exporter=output.exporter,
        )
        for name, output in spec.outputs.items()
    }
    state_name = _projection_state_name(spec.inputs, projections)
    if state_name in baseline.definitions:
        raise SpecError(
            f"notebook definition {state_name!r} collides with the transient state token",
            code="spec_definition_conflict",
            details={"definition": state_name},
        )
    return MatrixPlan(
        states=tuple(normalized),
        inputs=spec.inputs,
        outputs=tuple(spec.outputs),
        projections=projections,
        state_name=state_name,
        state_code=f"{state_name} = {normalized[0].fingerprint!r}",
    )


def _ordinary_packet(
    spec: ExportSpec,
    baseline: Baseline,
    inputs: Mapping[str, JsonValue],
) -> dict[str, object]:
    owners = {
        baseline.definitions[name].cell_id
        for name in spec.inputs
        if baseline.definitions[name].kind == "ordinary"
    }
    packet: dict[str, object] = {}
    for owner in owners:
        representative = next(
            definition
            for definition in baseline.definitions.values()
            if definition.cell_id == owner
        )
        for sibling in representative.siblings:
            try:
                packet[sibling] = baseline.definitions[sibling].value
            except KeyError as error:
                raise SpecError(
                    f"ordinary input cell {owner!r} has unavailable sibling {sibling!r}",
                    code="spec_input_sibling_missing",
                    details={"cell_id": owner, "definition": sibling},
                ) from error
    for name in spec.inputs:
        if baseline.definitions[name].kind == "ordinary":
            packet[name] = inputs[name]
    return packet


def projection_code(projection: OutputProjection, state_name: str) -> str:
    """Return one deterministic transient projection cell body."""

    if not isinstance(projection, OutputProjection):
        raise TypeError("projection must be an OutputProjection")
    if not isinstance(state_name, str) or not state_name.isidentifier():
        raise TypeError("state_name must be a Python identifier")
    label = json.dumps(projection.name, ensure_ascii=False)
    lines = [f"# marimo-export projection: {label}", state_name]
    if projection.exporter is None:
        lines.append(projection.source)
        return "\n".join(lines)
    reference = runtime_reference(projection.exporter.name)
    lines.extend(
        [
            "",
            f"from {reference.module} import {reference.symbol} as _marimo_export_exporter",
            "",
            f"_marimo_export_exporter({projection.source}{_render_options(projection.exporter)})",
        ]
    )
    return "\n".join(lines)


def _projection_state_name(
    inputs: tuple[str, ...],
    projections: Mapping[str, OutputProjection],
) -> str:
    outputs: JsonObject = {}
    for name, projection in projections.items():
        value: JsonObject = {"source": projection.source}
        if projection.exporter is not None:
            value["exporter"] = projection.exporter.to_value()
        outputs[name] = value
    payload = json_object(
        {
            "inputs": inputs,
            "outputs": outputs,
        },
        "projection plan",
    )
    suffix = sha256_bytes(canonical_bytes(payload))[:16]
    return f"marimo_export_state_{suffix}"


def _render_options(exporter: ExporterSpec) -> str:
    return "".join(
        f", {name}={_python_literal(value)}" for name, value in sorted(exporter.options.items())
    )


def _python_literal(value: FrozenOption) -> str:
    if value is None or isinstance(value, (bool, int, float, str)):
        return repr(value)
    if isinstance(value, tuple):
        return "[" + ", ".join(_python_literal(item) for item in value) + "]"
    return (
        "{"
        + ", ".join(f"{key!r}: {_python_literal(item)}" for key, item in sorted(value.items()))
        + "}"
    )
