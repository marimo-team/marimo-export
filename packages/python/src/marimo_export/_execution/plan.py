from __future__ import annotations

import ast
import io
import json
import token
import tokenize
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from marimo_export._control_roots import ControlRootCandidate, select_control_roots
from marimo_export._json import (
    JsonObject,
    JsonValue,
    canonical_bytes,
    json_object,
    portable_json_value,
    sha256_bytes,
)
from marimo_export.errors import SpecError
from marimo_export.exporters._definitions import runtime_reference
from marimo_export.exporters._spec import ExporterSpec
from marimo_export.index import ControlBinding, ControlPathStep
from marimo_export.planning import output_plan_sha256
from marimo_export.spec import (
    CellSource,
    ExportSource,
    ExportSpec,
    JsonSource,
    NativeSource,
    OutputSource,
    RenderedOutputSource,
)

if TYPE_CHECKING:
    from marimo_export.planning import ExportPlan as PublicExportPlan

_MAX_RUNTIME_ID_BYTES = 1_024


@dataclass(frozen=True, slots=True)
class Definition:
    """One live notebook definition used to plan an export."""

    name: str
    cell_id: str
    siblings: tuple[str, ...]
    kind: Literal["ordinary", "ui"]
    python_type: str
    value: object
    frontend_value: JsonValue | None = None
    portable_input: bool = True
    ui_patch: bool = False
    sensitive: bool = False
    domain: Mapping[str, JsonValue] = field(default_factory=lambda: MappingProxyType({}))
    control_paths: Mapping[str, tuple[ControlPathStep, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    input_dependencies: tuple[str, ...] = ()
    final_expression_bound: bool = False

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
        if not isinstance(self.portable_input, bool):
            raise TypeError("definition portable_input must be a boolean")
        if not isinstance(self.ui_patch, bool):
            raise TypeError("definition ui_patch must be a boolean")
        if not isinstance(self.final_expression_bound, bool):
            raise TypeError("definition final_expression_bound must be a boolean")
        if self.kind != "ui" and self.ui_patch:
            raise ValueError("only UI definitions can use patch updates")
        if not isinstance(self.control_paths, Mapping):
            raise TypeError("definition control_paths must be a mapping")
        parsed_control_paths: dict[str, tuple[ControlPathStep, ...]] = {}
        for control_id, path in self.control_paths.items():
            if (
                not isinstance(control_id, str)
                or not control_id
                or len(control_id.encode("utf-8")) > _MAX_RUNTIME_ID_BYTES
            ):
                raise ValueError("definition control IDs must be bounded non-empty strings")
            binding = ControlBinding(input=self.name, path=path)
            parsed_control_paths[control_id] = binding.path
        if self.kind == "ordinary" and parsed_control_paths:
            raise ValueError("ordinary definitions cannot have control paths")
        if (
            not isinstance(self.input_dependencies, tuple)
            or self.input_dependencies != tuple(sorted(set(self.input_dependencies)))
            or any(
                not isinstance(name, str)
                or not name.isidentifier()
                or len(name.encode("utf-8")) > _MAX_RUNTIME_ID_BYTES
                for name in self.input_dependencies
            )
        ):
            raise ValueError("definition input_dependencies must be sorted input names")
        object.__setattr__(
            self,
            "domain",
            MappingProxyType(json_object(self.domain, "definition domain")),
        )
        object.__setattr__(
            self,
            "control_paths",
            MappingProxyType(parsed_control_paths),
        )


@dataclass(frozen=True, slots=True)
class CellDefinition:
    """One native cell available for complete-cell projection."""

    id: str
    name: str | None
    code_sha256: str
    config: Mapping[str, JsonValue]
    input_dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise TypeError("cell id must be a non-empty string")
        if self.name is not None and (not isinstance(self.name, str) or not self.name):
            raise TypeError("cell name must be a non-empty string or None")
        if (
            not isinstance(self.code_sha256, str)
            or len(self.code_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.code_sha256)
        ):
            raise ValueError("cell code_sha256 must be a lowercase SHA-256 digest")
        object.__setattr__(
            self,
            "config",
            MappingProxyType(json_object(self.config, f"cell {self.id!r} config")),
        )
        if (
            not isinstance(self.input_dependencies, tuple)
            or self.input_dependencies != tuple(sorted(set(self.input_dependencies)))
            or any(
                not isinstance(name, str)
                or not name.isidentifier()
                or len(name.encode("utf-8")) > _MAX_RUNTIME_ID_BYTES
                for name in self.input_dependencies
            )
        ):
            raise ValueError("cell input_dependencies must be sorted input names")


@dataclass(frozen=True, slots=True)
class Baseline:
    """Live values and graph ownership captured before state execution."""

    definitions: Mapping[str, Definition]
    cells: tuple[CellDefinition, ...]
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
        if not isinstance(self.cells, tuple) or any(
            not isinstance(cell, CellDefinition) for cell in self.cells
        ):
            raise TypeError("baseline cells must contain CellDefinition values")
        ids = [cell.id for cell in self.cells]
        if len(ids) != len(set(ids)):
            raise ValueError("baseline cell IDs must be unique")


@dataclass(frozen=True, slots=True)
class NormalizedState:
    """One complete input vector plus values applied inside its state run."""

    aliases: tuple[str, ...]
    inputs: Mapping[str, JsonValue]
    fingerprint: str
    ordinary_values: Mapping[str, JsonValue]
    ui_updates: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.aliases, tuple)
            or not self.aliases
            or len(self.aliases) != len(set(self.aliases))
        ):
            raise ValueError("state aliases must be a nonempty unique tuple")
        object.__setattr__(
            self,
            "inputs",
            MappingProxyType(json_object(self.inputs, f"state {self.primary_alias!r} inputs")),
        )
        object.__setattr__(
            self,
            "ordinary_values",
            MappingProxyType(
                json_object(
                    self.ordinary_values,
                    f"state {self.primary_alias!r} ordinary values",
                )
            ),
        )
        object.__setattr__(
            self,
            "ui_updates",
            MappingProxyType(
                json_object(
                    self.ui_updates,
                    f"state {self.primary_alias!r} UI updates",
                )
            ),
        )

    @property
    def primary_alias(self) -> str:
        return self.aliases[0]


@dataclass(frozen=True, slots=True)
class PlannedOutput:
    """One export output and its transient representation."""

    name: str
    source: OutputSource
    exporter: ExporterSpec | None
    cell: CellDefinition | None = None
    owner_cell_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Normalized states and outputs ready for marimo-owned execution."""

    states: tuple[NormalizedState, ...]
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    planned_outputs: Mapping[str, PlannedOutput]
    ordinary_cells: Mapping[str, tuple[str, ...]]
    output_plan_sha256: str
    spec_sha256: str
    default_alias: str
    default_fingerprint: str
    baseline_fingerprint: str
    document_sha256: str
    state_name: str
    state_code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "planned_outputs", MappingProxyType(dict(self.planned_outputs)))
        object.__setattr__(
            self,
            "ordinary_cells",
            MappingProxyType(dict(self.ordinary_cells)),
        )
        for name in (
            "output_plan_sha256",
            "spec_sha256",
            "default_fingerprint",
            "baseline_fingerprint",
            "document_sha256",
        ):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"export plan {name} must be a lowercase SHA-256 digest")
        if not isinstance(self.default_alias, str) or not self.default_alias:
            raise ValueError("execution plan default_alias must be a nonempty string")
        defaults = [state for state in self.states if self.default_alias in state.aliases]
        if len(defaults) != 1 or defaults[0].fingerprint != self.default_fingerprint:
            raise ValueError("execution plan default alias and fingerprint must select one state")


def create_execution_plan(spec: ExportSpec, baseline: Baseline) -> ExecutionPlan:
    """Complete sparse rows and map ordinary inputs to their authored cells."""

    if not isinstance(spec, ExportSpec):
        raise TypeError("spec must be an ExportSpec")
    if not isinstance(baseline, Baseline):
        raise TypeError("baseline must be a Baseline")

    planned_outputs = _resolve_planned_outputs(spec, baseline)
    input_names = _infer_inputs(spec, baseline, planned_outputs)
    missing = sorted(set(input_names) - set(baseline.definitions))
    if missing:
        raise SpecError(
            f"notebook definitions are unavailable: {', '.join(missing)}",
            code="spec_definition_missing",
            details={"definitions": missing},
        )

    ambiguous_inputs = sorted(
        name
        for name in input_names
        if baseline.definitions[name].kind == "ordinary"
        and baseline.definitions[name].final_expression_bound
    )
    if ambiguous_inputs:
        raise SpecError(
            f"ordinary input {ambiguous_inputs[0]!r} is assigned by its cell's final expression",
            code="spec_input_invalid",
            details={"inputs": ambiguous_inputs},
        )

    baseline_inputs: JsonObject = {}
    for name in input_names:
        definition = baseline.definitions[name]
        if not definition.portable_input:
            raise SpecError(
                f"baseline input {name!r} is not portable",
                code="spec_input_invalid",
                details={"input": name, "python_type": definition.python_type},
            )
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
            baseline_inputs[name] = portable_json_value(value, f"baseline input {name!r}")
        except (TypeError, ValueError) as error:
            raise SpecError(
                f"baseline input {name!r} is not portable",
                code="spec_input_invalid",
                details={"input": name, "python_type": definition.python_type},
            ) from error

    wire_states = spec.to_value()["states"]
    assert isinstance(wire_states, dict)
    fingerprints: dict[bytes, int] = {}
    normalized: list[NormalizedState] = []
    for state_name in sorted(spec.states):
        authored = wire_states[state_name]
        assert isinstance(authored, dict)
        inputs = dict(baseline_inputs)
        for name, value in authored.items():
            definition = baseline.definitions[name]
            baseline_value = baseline_inputs[name]
            if definition.ui_patch and isinstance(baseline_value, dict) and isinstance(value, dict):
                inputs[name] = {**baseline_value, **value}
            else:
                inputs[name] = value
        vector = canonical_bytes(inputs)
        previous = fingerprints.get(vector)
        if previous is not None:
            current = normalized[previous]
            normalized[previous] = NormalizedState(
                aliases=(*current.aliases, state_name),
                inputs=current.inputs,
                fingerprint=current.fingerprint,
                ordinary_values=current.ordinary_values,
                ui_updates=current.ui_updates,
            )
            continue

        ordinary = {
            name: inputs[name]
            for name in input_names
            if baseline.definitions[name].kind == "ordinary"
        }
        ui_updates = {
            name: (
                authored[name]
                if baseline.definitions[name].ui_patch and name in authored
                else inputs[name]
            )
            for name in input_names
            if baseline.definitions[name].kind == "ui"
        }
        fingerprints[vector] = len(normalized)
        normalized.append(
            NormalizedState(
                aliases=(state_name,),
                inputs=inputs,
                fingerprint=sha256_bytes(vector),
                ordinary_values=ordinary,
                ui_updates=ui_updates,
            )
        )

    ordinary_cells = {
        cell_id: tuple(
            name
            for name in input_names
            if baseline.definitions[name].kind == "ordinary"
            and baseline.definitions[name].cell_id == cell_id
        )
        for cell_id in sorted(
            {
                baseline.definitions[name].cell_id
                for name in input_names
                if baseline.definitions[name].kind == "ordinary"
            }
        )
    }
    state_name = _state_token_name(input_names, planned_outputs)
    generated_names = {state_name}
    generated_names.update(
        snapshot_token_name(output)
        for output in planned_outputs.values()
        if isinstance(output.source, (CellSource, RenderedOutputSource))
    )
    conflicts = sorted(generated_names & set(baseline.definitions))
    if conflicts:
        raise SpecError(
            f"notebook definition {conflicts[0]!r} collides with a transient export token",
            code="spec_definition_conflict",
            details={"definition": conflicts[0]},
        )
    default_state = next(
        state.fingerprint for state in normalized if spec.default_state in state.aliases
    )
    return ExecutionPlan(
        states=tuple(normalized),
        inputs=input_names,
        outputs=tuple(spec.outputs),
        planned_outputs=planned_outputs,
        ordinary_cells=ordinary_cells,
        output_plan_sha256=output_plan_sha256(spec),
        spec_sha256=sha256_bytes(canonical_bytes(spec.to_value())),
        default_alias=spec.default_state,
        default_fingerprint=default_state,
        baseline_fingerprint=sha256_bytes(canonical_bytes(baseline_inputs)),
        document_sha256=baseline.document_sha256,
        state_name=state_name,
        state_code=f"{state_name} = {default_state!r}",
    )


def public_export_plan(
    plan: ExecutionPlan,
    *,
    producer_sha256: str,
    reusable_states: tuple[str, ...],
    exact_reuse: bool,
) -> PublicExportPlan:
    """Convert execution details into the stable public planning record."""

    from marimo_export.planning import ExportPlan, PlannedState

    if not isinstance(plan, ExecutionPlan):
        raise TypeError("plan must be an ExecutionPlan")
    if not isinstance(reusable_states, tuple):
        raise TypeError("reusable_states must be a tuple")
    if len(reusable_states) != len(set(reusable_states)):
        raise ValueError("reusable_states must contain unique fingerprints")
    reusable = tuple(sorted(reusable_states))
    state_fingerprints = {state.fingerprint for state in plan.states}
    if not set(reusable) <= state_fingerprints:
        raise ValueError("reusable states must belong to the execution plan")
    missing = tuple(sorted(state_fingerprints - set(reusable)))
    return ExportPlan(
        document_sha256=plan.document_sha256,
        producer_sha256=producer_sha256,
        output_plan_sha256=plan.output_plan_sha256,
        spec_sha256=plan.spec_sha256,
        default_alias=plan.default_alias,
        default_fingerprint=plan.default_fingerprint,
        inputs=plan.inputs,
        states=tuple(
            PlannedState(
                aliases=state.aliases,
                inputs=state.inputs,
                fingerprint=state.fingerprint,
            )
            for state in plan.states
        ),
        outputs=plan.outputs,
        reusable_states=reusable,
        missing_states=missing,
        exact_reuse=exact_reuse,
    )


def _resolve_planned_outputs(
    spec: ExportSpec,
    baseline: Baseline,
) -> dict[str, PlannedOutput]:
    planned: dict[str, PlannedOutput] = {}
    for name, output in spec.outputs.items():
        source = output.source
        if isinstance(source, CellSource):
            cell = _resolve_cell_source(source, baseline.cells)
            owner_cell_id = cell.id
        else:
            definition = baseline.definitions.get(source.selector.root)
            if definition is None:
                raise SpecError(
                    f"notebook definition {source.selector.root!r} is unavailable",
                    code="spec_definition_missing",
                    details={"definition": source.selector.root},
                )
            cell = None
            owner_cell_id = definition.cell_id
        planned[name] = PlannedOutput(
            name=name,
            source=source,
            exporter=output.exporter,
            cell=cell,
            owner_cell_id=owner_cell_id,
        )
    return planned


def _infer_inputs(
    spec: ExportSpec,
    baseline: Baseline,
    planned_outputs: Mapping[str, PlannedOutput],
) -> tuple[str, ...]:
    inferred = {name for state in spec.states.values() for name in state}
    relevant_ui: set[str] = set()

    def add_dependencies(names: tuple[str, ...]) -> None:
        for name in names:
            definition = baseline.definitions.get(name)
            if definition is not None and definition.kind == "ui":
                relevant_ui.add(name)
            else:
                inferred.add(name)

    for output in planned_outputs.values():
        source = output.source
        if isinstance(source, CellSource):
            if output.cell is None:
                raise ValueError("planned cell output has no resolved cell")
            add_dependencies(output.cell.input_dependencies)
            continue
        definition = baseline.definitions[source.selector.root]
        add_dependencies(definition.input_dependencies)
        if definition.kind == "ui":
            relevant_ui.add(definition.name)
    if relevant_ui:
        candidates = tuple(
            ControlRootCandidate(
                name=definition.name,
                control_ids=tuple(sorted(definition.control_paths)),
                input_dependencies=definition.input_dependencies,
                eligible=(
                    definition.portable_input
                    and not definition.sensitive
                    and bool(definition.control_paths)
                ),
            )
            for definition in baseline.definitions.values()
            if definition.kind == "ui"
        )
        inferred.update(select_control_roots(candidates, relevant=relevant_ui))
    return tuple(sorted(inferred))


def ordinary_cell_code(
    code: str,
    names: tuple[str, ...],
    values: Mapping[str, JsonValue],
) -> str:
    """Apply portable state values before one authored final expression."""

    if not isinstance(code, str):
        raise TypeError("cell code must be a string")
    if not names:
        return code
    assignments: list[str] = []
    for name in names:
        if not isinstance(name, str) or not name.isidentifier():
            raise TypeError("ordinary input names must be Python identifiers")
        try:
            value = values[name]
        except KeyError as error:
            raise ValueError(f"ordinary input {name!r} has no state value") from error
        assignments.append(f"{name} = {_python_literal(value)}")
    module = ast.parse(code)
    if _ends_with_semicolon(code):
        separator = "" if code.endswith("\n") else "\n"
        return code + separator + "\n".join(assignments) + ";\n"
    if not module.body or not isinstance(module.body[-1], ast.Expr):
        separator = "" if code.endswith("\n") else "\n"
        return code + separator + "\n".join(assignments) + "\n"
    expression = module.body[-1]
    offset = _source_offset(code, expression.lineno, expression.col_offset)
    return code[:offset] + "; ".join(assignments) + "; " + code[offset:]


def _ends_with_semicolon(code: str) -> bool:
    try:
        tokens = list(tokenize.tokenize(io.BytesIO(code.strip().encode("utf-8")).readline))
    except (tokenize.TokenError, SyntaxError):
        return code.rstrip().endswith(";")
    ignored = {
        token.ENDMARKER,
        token.NEWLINE,
        tokenize.NL,
        token.COMMENT,
        token.INDENT,
        token.DEDENT,
        token.ENCODING,
    }
    return next(
        (item.string == ";" for item in reversed(tokens) if item.type not in ignored), False
    )


def _source_offset(code: str, lineno: int, byte_column: int) -> int:
    lines = code.splitlines(keepends=True)
    line_index = lineno - 1
    prefix = lines[line_index].encode("utf-8")[:byte_column].decode("utf-8")
    return sum(len(line) for line in lines[:line_index]) + len(prefix)


def output_cell_code(
    planned_output: PlannedOutput,
    state_name: str,
    *,
    implementation_identity: str,
    document_sha256: str,
    producer_identity: str,
    exporter_identity: str | None = None,
    exporter_token: str | None = None,
) -> str:
    """Return one deterministic transient output cell body."""

    if not isinstance(planned_output, PlannedOutput):
        raise TypeError("planned_output must be a PlannedOutput")
    if not isinstance(state_name, str) or not state_name.isidentifier():
        raise TypeError("state_name must be a Python identifier")
    if (
        not isinstance(implementation_identity, str)
        or len(implementation_identity) != 64
        or any(character not in "0123456789abcdef" for character in implementation_identity)
    ):
        raise ValueError("implementation_identity must be a lowercase SHA-256 digest")
    if (
        not isinstance(document_sha256, str)
        or len(document_sha256) != 64
        or any(character not in "0123456789abcdef" for character in document_sha256)
    ):
        raise ValueError("document_sha256 must be a lowercase SHA-256 digest")
    if not isinstance(producer_identity, str) or not producer_identity:
        raise ValueError("producer_identity must be a non-empty string")
    projection_identity = planned_output_identity(planned_output)
    label = json.dumps(planned_output.name, ensure_ascii=False)
    lines = [
        f"# marimo-export output: {label}",
        state_name,
        f"_marimo_export_implementation_identity = {'sha256:' + implementation_identity!r}",
        f"_marimo_export_document_identity = {'sha256:' + document_sha256!r}",
        f"_marimo_export_producer_identity = {producer_identity!r}",
        f"_marimo_export_projection_identity = {projection_identity!r}",
        "",
    ]
    source = planned_output.source
    if isinstance(source, CellSource):
        if (
            exporter_identity is not None
            or exporter_token is not None
            or planned_output.exporter is not None
        ):
            raise ValueError("cell outputs cannot have an exporter identity")
        lines.extend(
            [
                "from marimo_export._marimo.compat.projections import "
                "capture_materialized_cell as _marimo_export_capture_cell",
                "",
                f"_marimo_export_capture_cell({snapshot_token_name(planned_output)})",
            ]
        )
        return "\n".join(lines)
    if isinstance(source, RenderedOutputSource):
        if (
            exporter_identity is not None
            or exporter_token is not None
            or planned_output.exporter is not None
        ):
            raise ValueError("rendered outputs cannot have an exporter identity")
        lines.extend(
            [
                "from marimo_export._marimo.compat.projections import "
                "capture_materialized_output as _marimo_export_capture_output",
                "",
                f"_marimo_export_capture_output({snapshot_token_name(planned_output)})",
            ]
        )
        return "\n".join(lines)
    selector = source.selector
    path = tuple((step.kind, step.key) for step in selector.path)
    if isinstance(source, JsonSource):
        if exporter_identity is not None or exporter_token is not None:
            raise ValueError("JSON outputs cannot have an exporter identity")
        lines.extend(
            [
                "from marimo_export._marimo.compat.projections import "
                "capture_json_value as _marimo_export_capture_json",
                "",
                f"_marimo_export_capture_json({selector.root}, {path!r})",
            ]
        )
        return "\n".join(lines)
    if isinstance(source, NativeSource):
        if exporter_identity is not None or exporter_token is not None:
            raise ValueError("native outputs cannot have an exporter identity")
        lines.extend(
            [
                "from marimo_export._marimo.compat.projections import "
                "capture_native_value as _marimo_export_capture_native",
                "",
                f"_marimo_export_capture_native({selector.root}, {path!r})",
            ]
        )
        return "\n".join(lines)
    if not isinstance(source, ExportSource) or planned_output.exporter is None:
        raise ValueError("export source has no exporter")
    if (
        not isinstance(exporter_identity, str)
        or len(exporter_identity) != 64
        or any(character not in "0123456789abcdef" for character in exporter_identity)
    ):
        raise ValueError("exporter_identity must be a lowercase SHA-256 digest")
    reference = runtime_reference(planned_output.exporter.name)
    identity_literal = repr(f"sha256:{exporter_identity}")
    lines.extend([f"_marimo_export_exporter_identity = {identity_literal}", ""])
    if ":" in planned_output.exporter.name:
        expected_token = exporter_token_name(planned_output.exporter)
        if exporter_token != expected_token:
            raise ValueError("custom exporter token does not match its specification")
        lines.extend(
            [
                "from marimo_export._marimo.compat.exporters import "
                "invoke_prepared_exporter as _marimo_export_invoke_exporter",
                "",
            ]
        )
        call = (
            f"_marimo_export_invoke_exporter({expected_token!r}, "
            f"_marimo_export_resolve_value({selector.root}, {path!r}), "
            f"{_python_literal(planned_output.exporter.options)})"
        )
    else:
        if exporter_token is not None:
            raise ValueError("built-in exporters cannot have a transient token")
        lines.extend(
            [
                f"from {reference.module} import {reference.symbol} as _marimo_export_exporter",
                "",
            ]
        )
        call = (
            f"_marimo_export_exporter(_marimo_export_resolve_value({selector.root}, "
            f"{path!r}){_render_options(planned_output.exporter)})"
        )
    lines.extend(
        [
            "from marimo_export._marimo.compat.projections import "
            "resolve_value_path as _marimo_export_resolve_value",
            "from marimo_export._marimo.blob import "
            "to_native_blob_asset as _marimo_export_native_blob_asset",
            "",
        ]
    )
    lines.append(f"_marimo_export_native_blob_asset({call})")
    return "\n".join(lines)


def planned_output_identity(planned_output: PlannedOutput) -> str:
    value: JsonObject = {
        "name": planned_output.name,
        "source": _planned_source_value(planned_output),
    }
    if planned_output.exporter is not None:
        value["exporter"] = planned_output.exporter.to_value()
    return sha256_bytes(canonical_bytes(value))


def exporter_token_name(exporter: ExporterSpec) -> str:
    """Return the deterministic registry token for one custom exporter."""

    if ":" not in exporter.name:
        raise ValueError("only custom exporters have transient callable tokens")
    identity = sha256_bytes(
        canonical_bytes(
            {
                "name": exporter.name,
                "dependencies": list(exporter.dependencies),
            }
        )
    )
    return f"marimo_export_exporter_{identity}"


def snapshot_token_name(planned_output: PlannedOutput) -> str:
    if not isinstance(planned_output.source, (CellSource, RenderedOutputSource)):
        raise ValueError("only Marimo snapshot outputs have snapshot tokens")
    return f"marimo_export_snapshot_{planned_output_identity(planned_output)}"


def snapshot_token_code(planned_output: PlannedOutput) -> str:
    name = snapshot_token_name(planned_output)
    return f"{name} = b''"


def _state_token_name(
    inputs: tuple[str, ...],
    planned_outputs: Mapping[str, PlannedOutput],
) -> str:
    outputs: JsonObject = {}
    for name, planned_output in planned_outputs.items():
        value: JsonObject = {"source": _planned_source_value(planned_output)}
        if planned_output.exporter is not None:
            value["exporter"] = planned_output.exporter.to_value()
        outputs[name] = value
    payload = json_object(
        {
            "inputs": inputs,
            "outputs": outputs,
        },
        "export plan",
    )
    suffix = sha256_bytes(canonical_bytes(payload))[:16]
    return f"marimo_export_state_{suffix}"


def _planned_source_value(planned_output: PlannedOutput) -> JsonObject:
    source = planned_output.source
    if isinstance(source, JsonSource):
        return {"kind": "json", "selector": source.selector.source}
    if isinstance(source, NativeSource):
        return {"kind": "native", "selector": source.selector.source}
    if isinstance(source, ExportSource):
        return {"kind": "export", "selector": source.selector.source}
    if isinstance(source, RenderedOutputSource):
        return {"kind": "output", "selector": source.selector.source}
    cell = planned_output.cell
    if cell is None:
        raise ValueError("cell output has no resolved cell")
    return {
        "kind": "cell",
        "id": cell.id,
        "code_sha256": cell.code_sha256,
    }


def _resolve_cell_source(
    source: CellSource,
    cells: tuple[CellDefinition, ...],
) -> CellDefinition:
    matches = [
        cell for cell in cells if (cell.name if source.by == "name" else cell.id) == source.value
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SpecError(
            f"notebook cell {source.by} {source.value!r} is unavailable",
            code="spec_definition_missing",
            details={"cell": source.value, "by": source.by},
        )
    raise SpecError(
        f"notebook cell name {source.value!r} is ambiguous",
        code="spec_output_invalid",
        details={"cell": source.value, "matches": [cell.id for cell in matches]},
    )


def _render_options(exporter: ExporterSpec) -> str:
    return "".join(
        f", {name}={_python_literal(value)}" for name, value in sorted(exporter.options.items())
    )


def _python_literal(value: object) -> str:
    if value is None or isinstance(value, (bool, int, float, str)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_python_literal(item) for item in value) + "]"
    if not isinstance(value, Mapping):
        raise TypeError(f"portable value has unsupported type {type(value).__name__}")
    return (
        "{"
        + ", ".join(f"{key!r}: {_python_literal(item)}" for key, item in sorted(value.items()))
        + "}"
    )
