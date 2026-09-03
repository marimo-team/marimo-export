from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TypeAlias

from marimo_export._json import JsonValue, json_object, json_value
from marimo_export.errors import SpecError
from marimo_export.exporters._definitions import normalize_exporter

FrozenOption: TypeAlias = (
    str | int | float | bool | tuple["FrozenOption", ...] | Mapping[str, "FrozenOption"] | None
)


@dataclass(frozen=True, slots=True, init=False)
class ExporterSpec:
    """Select an importable conversion, options, and source dependencies."""

    name: str
    options: Mapping[str, FrozenOption]
    dependencies: tuple[str, ...]
    _wire: JsonValue = field(repr=False)

    def __init__(
        self,
        name: str,
        *,
        options: Mapping[str, JsonValue] | None = None,
        dependencies: tuple[str, ...] = (),
    ) -> None:
        try:
            normalized_name, normalized_options, normalized_dependencies = normalize_exporter(
                name,
                {} if options is None else options,
                dependencies,
            )
        except (TypeError, ValueError) as error:
            raise SpecError(
                f"invalid exporter: {error}",
                code="spec_exporter_invalid",
            ) from error
        wire: JsonValue = (
            normalized_name
            if ":" not in normalized_name and not normalized_options and not normalized_dependencies
            else {
                "name": normalized_name,
                "options": normalized_options,
                "dependencies": list(normalized_dependencies),
            }
        )
        object.__setattr__(self, "name", normalized_name)
        object.__setattr__(
            self,
            "options",
            MappingProxyType({key: _freeze(value) for key, value in normalized_options.items()}),
        )
        object.__setattr__(self, "dependencies", normalized_dependencies)
        object.__setattr__(self, "_wire", wire)

    @classmethod
    def from_value(cls, value: object) -> ExporterSpec:
        """Normalize a string shorthand or exporter object."""

        if isinstance(value, ExporterSpec):
            return value
        if isinstance(value, str):
            if ":" in value:
                raise SpecError(
                    "invalid exporter: custom exporter objects must declare "
                    "'name', 'options', and 'dependencies'",
                    code="spec_exporter_invalid",
                )
            return cls(value)
        try:
            document = json_object(value, "exporter")
        except (TypeError, ValueError) as error:
            raise SpecError(
                f"invalid exporter: {error}",
                code="spec_exporter_invalid",
            ) from error
        if set(document) != {"name", "options", "dependencies"}:
            raise SpecError(
                "invalid exporter: object must contain exactly "
                "'name', 'options', and 'dependencies'",
                code="spec_exporter_invalid",
            )
        name = document["name"]
        options = document["options"]
        dependencies = document["dependencies"]
        if (
            not isinstance(name, str)
            or not isinstance(options, dict)
            or not isinstance(dependencies, list)
        ):
            raise SpecError(
                "invalid exporter: 'name' must be a string, 'options' must be an object, "
                "and 'dependencies' must be an array",
                code="spec_exporter_invalid",
            )
        return cls(name, options=options, dependencies=tuple(dependencies))

    def to_value(self) -> JsonValue:
        """Return the normalized wire value."""

        return json_value(self._wire, "exporter")


def importable(
    name: str,
    *,
    options: Mapping[str, JsonValue] | None = None,
    dependencies: tuple[str, ...] = (),
) -> ExporterSpec:
    """Select a non-cacheable importable callable and its source modules."""

    exporter = ExporterSpec(name, options=options, dependencies=dependencies)
    if ":" not in exporter.name:
        raise SpecError(
            "invalid exporter: importable() requires a 'module:symbol' name",
            code="spec_exporter_invalid",
        )
    return exporter


def builtin(name: str, options: Mapping[str, JsonValue] | None = None) -> ExporterSpec:
    return ExporterSpec(name, options=options)


def _freeze(value: JsonValue) -> FrozenOption:
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    return value


__all__ = ["ExporterSpec", "importable"]
