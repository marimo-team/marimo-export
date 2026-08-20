"""Immutable records returned by notebook inspection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, cast

import marimo_export._control_roots as _control_roots
from marimo_export._json import (
    JsonObject,
    JsonValue,
    canonical_bytes,
    decode_json,
    json_object,
)
from marimo_export.errors import SessionError, SpecError
from marimo_export.index import ControlBinding, ControlPathStep
from marimo_export.spec import (
    CellSource,
    FrozenJsonObject,
    FrozenJsonValue,
    OutputSpec,
    RenderedOutputSource,
    StrPath,
    ValueSource,
)


@dataclass(frozen=True, slots=True, init=False)
class DefinitionDescription:
    """One notebook definition visible to export preflight."""

    name: str
    cell_id: str
    python_type: str
    kind: Literal["ordinary", "ui"]
    input_mode: Literal["value", "patch"]
    siblings: tuple[str, ...]
    portable_input: bool
    sensitive: bool
    value_available: bool
    control_paths: Mapping[str, tuple[ControlPathStep, ...]]
    input_dependencies: tuple[str, ...]
    _value_bytes: bytes = field(repr=False)
    _domain_bytes: bytes = field(repr=False)

    def __init__(
        self,
        *,
        name: str,
        cell_id: str,
        python_type: str,
        kind: Literal["ordinary", "ui"],
        input_mode: Literal["value", "patch"],
        siblings: tuple[str, ...],
        portable_input: bool,
        sensitive: bool,
        value_available: bool,
        control_paths: Mapping[str, tuple[ControlPathStep, ...]],
        input_dependencies: tuple[str, ...],
        value: JsonValue,
        domain: Mapping[str, JsonValue],
    ) -> None:
        if not isinstance(name, str) or not name.isidentifier():
            raise SessionError("definition name must be a Python identifier")
        if not all(isinstance(item, str) and item for item in (cell_id, python_type)):
            raise SessionError("definition cell_id and python_type must be non-empty strings")
        if kind not in {"ordinary", "ui"}:
            raise SessionError("definition kind must be ordinary or ui")
        if input_mode not in {"value", "patch"}:
            raise SessionError("definition input_mode must be value or patch")
        if kind != "ui" and input_mode != "value":
            raise SessionError("ordinary definitions must use value input mode")
        if not isinstance(control_paths, Mapping):
            raise SessionError("definition control_paths must be a mapping")
        if kind == "ordinary" and control_paths:
            raise SessionError("ordinary definitions cannot have control paths")
        parsed_control_paths: dict[str, tuple[ControlPathStep, ...]] = {}
        for control_id, path in control_paths.items():
            if (
                not isinstance(control_id, str)
                or not control_id
                or len(control_id.encode("utf-8")) > 1_024
            ):
                raise SessionError("definition control IDs must be bounded non-empty strings")
            try:
                parsed_control_paths[control_id] = ControlBinding(
                    input=name,
                    path=path,
                ).path
            except (TypeError, ValueError) as error:
                raise SessionError("definition control path is invalid") from error
        if (
            not isinstance(input_dependencies, tuple)
            or input_dependencies != tuple(sorted(set(input_dependencies)))
            or any(
                not isinstance(dependency, str)
                or not dependency.isidentifier()
                or len(dependency.encode("utf-8")) > 255
                for dependency in input_dependencies
            )
        ):
            raise SessionError("definition input_dependencies must be sorted input names")
        if (
            not isinstance(siblings, tuple)
            or name not in siblings
            or len(siblings) != len(set(siblings))
        ):
            raise SessionError("definition siblings are invalid")
        for label, flag in (
            ("portable_input", portable_input),
            ("sensitive", sensitive),
            ("value_available", value_available),
        ):
            if not isinstance(flag, bool):
                raise SessionError(f"definition {label} must be a boolean")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "cell_id", cell_id)
        object.__setattr__(self, "python_type", python_type)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "input_mode", input_mode)
        object.__setattr__(self, "siblings", siblings)
        object.__setattr__(self, "portable_input", portable_input)
        object.__setattr__(self, "sensitive", sensitive)
        object.__setattr__(self, "value_available", value_available)
        object.__setattr__(self, "control_paths", MappingProxyType(parsed_control_paths))
        object.__setattr__(self, "input_dependencies", input_dependencies)
        object.__setattr__(self, "_value_bytes", canonical_bytes(value))
        object.__setattr__(
            self,
            "_domain_bytes",
            canonical_bytes(json_object(domain, "definition domain")),
        )

    @property
    def value(self) -> FrozenJsonValue | None:
        if not self.value_available:
            return None
        return cast(FrozenJsonValue, _freeze(decode_json(self._value_bytes, "definition value")))

    @property
    def domain(self) -> FrozenJsonObject:
        return cast(
            FrozenJsonObject,
            _freeze(decode_json(self._domain_bytes, "definition domain")),
        )

    def to_dict(self) -> JsonObject:
        return {
            "name": self.name,
            "cell_id": self.cell_id,
            "python_type": self.python_type,
            "kind": self.kind,
            "input_mode": self.input_mode,
            "siblings": list(self.siblings),
            "portable_input": self.portable_input,
            "sensitive": self.sensitive,
            "value_available": self.value_available,
            "control_paths": {
                object_id: [step.to_value() for step in path]
                for object_id, path in self.control_paths.items()
            },
            "input_dependencies": list(self.input_dependencies),
            "value": _thaw(self.value) if self.value_available else None,
            "domain": cast(JsonObject, _thaw(self.domain)),
        }


@dataclass(frozen=True, slots=True, init=False)
class CellDescription:
    """One authored cell available to complete-cell projections."""

    id: str
    name: str | None
    code_sha256: str
    input_dependencies: tuple[str, ...]
    _config_bytes: bytes = field(repr=False)

    def __init__(
        self,
        *,
        id: str,
        name: str | None,
        code_sha256: str,
        config: Mapping[str, JsonValue],
        input_dependencies: tuple[str, ...],
    ) -> None:
        if not isinstance(id, str) or not id or len(id.encode("utf-8")) > 1_024:
            raise SessionError("cell id must be a bounded non-empty string")
        if name is not None and (
            not isinstance(name, str) or not name or len(name.encode("utf-8")) > 255
        ):
            raise SessionError("cell name must be a bounded non-empty string or null")
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "code_sha256", _digest(code_sha256, "cell code digest"))
        if (
            not isinstance(input_dependencies, tuple)
            or input_dependencies != tuple(sorted(set(input_dependencies)))
            or any(
                not isinstance(dependency, str) or not dependency.isidentifier()
                for dependency in input_dependencies
            )
        ):
            raise SessionError("cell input_dependencies must be sorted input names")
        object.__setattr__(self, "input_dependencies", input_dependencies)
        object.__setattr__(
            self,
            "_config_bytes",
            canonical_bytes(json_object(config, "cell config")),
        )

    @property
    def config(self) -> FrozenJsonObject:
        return cast(
            FrozenJsonObject,
            _freeze(decode_json(self._config_bytes, "cell config")),
        )

    def to_dict(self) -> JsonObject:
        return {
            "id": self.id,
            "name": self.name,
            "code_sha256": self.code_sha256,
            "config": cast(JsonObject, _thaw(self.config)),
            "input_dependencies": list(self.input_dependencies),
        }


@dataclass(frozen=True, slots=True)
class SessionDescription:
    """Definitions and cells available in one selected marimo session."""

    session_id: str
    filename: str | None
    path: str | None
    document_sha256: str
    marimo_version: str
    marimo_export_version: str
    implementation_sha256: str
    capabilities: tuple[str, ...]
    definitions: tuple[DefinitionDescription, ...]
    cells: tuple[CellDescription, ...]

    def input_roots(self) -> tuple[str, ...]:
        """Return canonical eligible UI roots from the complete definition set."""

        return _control_roots.select_control_roots(_control_candidates(self.definitions))

    def inputs_for(self, outputs: Mapping[str, OutputSpec]) -> tuple[str, ...]:
        """Return canonical portable UI roots affecting selected outputs."""

        if not isinstance(outputs, Mapping) or not outputs:
            raise TypeError("outputs must be a nonempty mapping of OutputSpec values")
        definitions = {definition.name: definition for definition in self.definitions}
        cells_by_id = {cell.id: cell for cell in self.cells}
        cells_by_name: dict[str, list[CellDescription]] = {}
        for cell in self.cells:
            if cell.name is not None:
                cells_by_name.setdefault(cell.name, []).append(cell)
        relevant: set[str] = set()
        for output_name, output in outputs.items():
            if not isinstance(output_name, str) or not isinstance(output, OutputSpec):
                raise TypeError("outputs must map names to OutputSpec values")
            source = output.source
            if isinstance(source, (ValueSource, RenderedOutputSource)):
                root = source.selector.root
                definition = definitions.get(root)
                if definition is None:
                    raise SpecError(
                        f"notebook definition {root!r} is unavailable",
                        code="spec_definition_missing",
                        details={"definition": root},
                    )
                relevant.update(definition.input_dependencies)
                if definition.kind == "ui":
                    relevant.add(definition.name)
                continue
            if not isinstance(source, CellSource):
                raise TypeError("output source is invalid")
            if source.by == "id":
                cell = cells_by_id.get(source.value)
                matches = [] if cell is None else [cell]
            else:
                matches = cells_by_name.get(source.value, [])
            if len(matches) != 1:
                raise SpecError(
                    f"notebook cell {source.by} {source.value!r} is unavailable or ambiguous",
                    code="spec_definition_missing" if not matches else "spec_output_invalid",
                    details={"cell": source.value, "by": source.by},
                )
            relevant.update(matches[0].input_dependencies)

        return _control_roots.select_control_roots(
            _control_candidates(self.definitions),
            relevant=relevant,
        )

    def to_dict(self) -> JsonObject:
        return {
            "session_id": self.session_id,
            "filename": self.filename,
            "path": self.path,
            "document_sha256": self.document_sha256,
            "marimo_version": self.marimo_version,
            "marimo_export_version": self.marimo_export_version,
            "implementation_sha256": self.implementation_sha256,
            "capabilities": list(self.capabilities),
            "definitions": [definition.to_dict() for definition in self.definitions],
            "cells": [cell.to_dict() for cell in self.cells],
        }


def inspect_notebook(source: StrPath, *, timeout: float = 30.0) -> SessionDescription:
    """Run a notebook's initial autorun and return its export definitions."""

    from marimo_export.producer import open_notebook

    with open_notebook(source, timeout=timeout) as producer:
        return producer.inspect()


def _digest(value: object, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise SessionError(f"{path} must be a non-empty string")
    digest = value
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise SessionError(f"{path} must be a lowercase SHA-256 digest")
    return digest


def _control_candidates(
    definitions: tuple[DefinitionDescription, ...],
) -> tuple[_control_roots.ControlRootCandidate, ...]:
    return tuple(
        _control_roots.ControlRootCandidate(
            name=definition.name,
            control_ids=tuple(sorted(definition.control_paths)),
            input_dependencies=definition.input_dependencies,
            eligible=(
                definition.portable_input
                and not definition.sensitive
                and definition.value_available
                and bool(definition.control_paths)
            ),
        )
        for definition in definitions
        if definition.kind == "ui"
    )


def _freeze(value: JsonValue) -> FrozenJsonValue:
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    return value


def _thaw(value: FrozenJsonValue) -> JsonValue:
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, Mapping):
        items = cast(Mapping[str, FrozenJsonValue], value)
        return {key: _thaw(item) for key, item in items.items()}
    return value


__all__ = [
    "CellDescription",
    "DefinitionDescription",
    "SessionDescription",
    "inspect_notebook",
]
