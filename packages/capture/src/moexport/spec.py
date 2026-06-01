"""Pydantic models for the static export specification."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from json import JSONDecodeError
from pathlib import Path
from typing import Annotated, Any, Literal, TypeAlias, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)
import yaml

from moexport.sources import SourceSpec, normalize_source

SpecKey: TypeAlias = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]*$")]
StateKey: TypeAlias = Annotated[
    str,
    Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"),
]
JsonConfigValue: TypeAlias = (
    str | int | float | bool | None | list[Any] | dict[str, Any]
)
JsonConfigObject: TypeAlias = dict[str, Any]


class SpecModel(BaseModel):
    """Base model for user-authored export specs.

    Specs are public API. Unknown keys raise validation errors so typos do not
    change the authored export silently.
    """

    model_config = ConfigDict(extra="forbid")


class RefExport(SpecModel):
    """Exporter callable loaded from the Python environment."""

    type: Literal["ref"] = Field(
        description="Discriminator for an exporter loaded by Python import reference.",
    )
    ref: str = Field(
        description="Python import reference in `module:object` form.",
    )

    @field_validator("ref")
    @classmethod
    def _ref_must_be_resolvable_shape(cls, value: str) -> str:
        module, sep, obj = value.partition(":")
        if not sep or not module or not obj:
            raise ValueError("export ref must use 'module:object' syntax")
        return value


class CodeExport(SpecModel):
    """Exporter callable defined by inline Python source."""

    type: Literal["code"] = Field(
        description="Discriminator for an exporter defined by inline Python source.",
    )
    code: str = Field(
        description=(
            "Python source evaluated in a temporary module namespace. "
            "It must define a callable named `export`."
        ),
    )

    @field_validator("code")
    @classmethod
    def _code_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("export code must not be empty")
        return value


ExportCallable: TypeAlias = Annotated[
    RefExport | CodeExport,
    Field(discriminator="type"),
]
SpecFormat: TypeAlias = Literal["json", "yaml"]

BUILTIN_FORMAT_EXPORTERS = {
    "json": "moexport.exporters.core:json",
    "text": "moexport.exporters.core:text",
    "html": "moexport.exporters.core:html",
    "arrow": "moexport.exporters.dataframe:arrow",
    "parquet": "moexport.exporters.dataframe:parquet",
    "vegalite": "moexport.exporters.altair:vegalite",
    "png": "moexport.exporters.altair:png",
    "anywidget": "moexport.exporters.anywidget:bundle",
    "display": "moexport.exporters.display:display_json",
    "display_json": "moexport.exporters.display:display_json",
    "markdown": "moexport.exporters.display:markdown",
}


class CodeStateValue(SpecModel):
    """Scenario state value computed from a Python expression."""

    code: str = Field(
        description=(
            "Python expression evaluated in the live notebook runtime before "
            "calling `mox.evaluate`."
        ),
    )

    @field_validator("code")
    @classmethod
    def _code_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("state expression must not be empty")
        return value


def _validate_state_value(value: object) -> object:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        if "code" in mapping:
            return CodeStateValue.model_validate(dict(mapping))
    return _validate_json_value(value)


StateValue: TypeAlias = CodeStateValue | JsonConfigValue


class FormatSpec(SpecModel):
    """One named format to produce for a value."""

    export: ExportCallable = Field(
        description="Callable that projects the live Python value into a format.",
    )
    options: JsonConfigObject = Field(
        default_factory=dict,
        description="JSON-shaped options passed to the exporter callable.",
    )

    @field_validator("options", mode="before")
    @classmethod
    def _options_must_be_json_object(cls, value: object) -> object:
        if value is None:
            return {}

        validated = _validate_json_value(value)
        if not isinstance(validated, dict):
            raise ValueError("format options must be a JSON object")
        return validated


class ValueSpec(SpecModel):
    """Named source whose result should be exported."""

    source: SourceSpec = Field(
        description=(
            "Source evaluated by `mox.capture`, for example `df.head(10)`, "
            "`{def: df}`, or `{cell: intro}`."
        ),
    )
    formats: Annotated[
        dict[SpecKey, FormatSpec],
        Field(
            min_length=1,
            description="Named formats to produce for this value.",
        ),
    ]

    @model_validator(mode="before")
    @classmethod
    def _accept_product_shorthand(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value

        normalized = dict(value)
        if "source" in normalized:
            normalized["source"] = normalize_source(normalized["source"])
        if "formats" in normalized:
            normalized["formats"] = _normalize_formats(normalized["formats"])
        return normalized

    @field_validator("source", mode="before")
    @classmethod
    def _source_must_be_typed(cls, value: object) -> object:
        return normalize_source(value)


class ScenarioSpec(SpecModel):
    """One named finite notebook state to materialize.

    `state` overrides notebook definitions. Dotted state keys patch object
    paths after producer cells run.
    """

    id: SpecKey = Field(
        default="default",
        description="Stable id used in format paths and manifest lookup.",
    )
    state: dict[StateKey, StateValue] = Field(
        default_factory=dict,
        description=(
            "Scenario state. Bare keys override definitions, and dotted keys "
            "patch object attributes such as `selector.value`."
        ),
    )

    @field_validator("state", mode="before")
    @classmethod
    def _state_must_be_json_or_code(cls, value: object) -> object:
        return _validate_value_mapping(value, "scenario state")


class ProvenanceSpec(SpecModel):
    """Producer-controlled provenance policy for bundle records."""

    source: Literal["none", "hash", "source"] = Field(
        default="hash",
        description=(
            "Notebook source policy. `hash` records only the source hash, "
            "`source` stores source as a bundle blob, and `none` omits both."
        ),
    )
    spec: Literal["none", "hash", "embed"] = Field(
        default="embed",
        description="Source spec policy for manifest provenance.",
    )


def _validate_value_mapping(value: object, label: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")

    validated: dict[str, object] = {}
    for key, state_value in value.items():
        if not isinstance(key, str):
            raise ValueError(f"{label} keys must be strings")
        validated[key] = _validate_state_value(state_value)
    return validated


def _normalize_formats(value: object) -> object:
    if isinstance(value, list):
        formats: dict[str, object] = {}
        for index, item in enumerate(value):
            name, spec = _normalize_format_item(item, f"formats[{index}]")
            if name in formats:
                raise ValueError(f"duplicate format {name!r}")
            formats[name] = spec
        return formats

    if isinstance(value, Mapping):
        return {
            _required_string(name, "format name"): _normalize_format_mapping(
                _required_string(name, "format name"),
                item,
            )
            for name, item in value.items()
        }

    return value


def _normalize_format_item(value: object, label: str) -> tuple[str, object]:
    if isinstance(value, str):
        return value, _builtin_format(value, {})
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a format name or object")

    mapping = cast(Mapping[object, object], value)
    if "format" in mapping:
        name = _required_string(mapping["format"], f"{label}.format")
        options = mapping.get("options", {})
        return name, _builtin_format(name, options)

    if len(mapping) != 1:
        raise ValueError(f"{label} must contain exactly one format name")
    name, item = next(iter(mapping.items()))
    format_name = _required_string(name, "format name")
    return format_name, _normalize_format_mapping(format_name, item)


def _normalize_format_mapping(name: str, value: object) -> object:
    if isinstance(value, Mapping) and "export" in value:
        return value
    if name in BUILTIN_FORMAT_EXPORTERS:
        return _builtin_format(name, {} if value is None else value)
    return value


def _builtin_format(name: str, options: object) -> dict[str, object]:
    try:
        ref = BUILTIN_FORMAT_EXPORTERS[name]
    except KeyError as error:
        raise ValueError(
            f"unknown built-in format {name!r}. Provide an explicit export config."
        ) from error
    return {
        "export": {
            "type": "ref",
            "ref": ref,
        },
        "options": options,
    }


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


class ExportSpec(SpecModel):
    """Complete declaration of the values and scenarios to export."""

    scenarios: list[ScenarioSpec] = Field(
        default_factory=lambda: [ScenarioSpec()],
        description="Explicit scenario matrix. Omitted means one default scenario.",
    )
    provenance: ProvenanceSpec = Field(
        default_factory=ProvenanceSpec,
        description="Notebook source and source-spec provenance policy.",
    )
    values: Annotated[
        dict[SpecKey, ValueSpec],
        Field(
            min_length=1,
            description="Named values to evaluate and project into formats.",
        ),
    ]

    @model_validator(mode="after")
    def _scenario_ids_must_be_unique(self) -> ExportSpec:
        ids = [scenario.id for scenario in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("scenario ids must be unique")
        return self


EXPORT_SPEC_ADAPTER = TypeAdapter(ExportSpec)


def parse_export_spec(value: object) -> ExportSpec:
    """Validate a Python dict loaded from JSON/YAML into an export spec."""

    return EXPORT_SPEC_ADAPTER.validate_python(value)


def parse_export_spec_text(
    text: str,
    *,
    format: SpecFormat | None = None,
    source: str | Path | None = None,
) -> ExportSpec:
    """Parse a JSON or YAML spec string and validate it."""

    parsed_format = format or _infer_spec_format(source)
    if parsed_format == "json":
        value = json.loads(text)
    elif parsed_format == "yaml":
        value = yaml.safe_load(text)
    else:
        value = _parse_json_or_yaml(text)

    return parse_export_spec(value)


def load_export_spec(path: str | Path) -> ExportSpec:
    """Read a JSON or YAML spec file and validate it."""

    source = Path(path)
    return parse_export_spec_text(
        source.read_text(encoding="utf-8"),
        source=source,
    )


def _infer_spec_format(source: str | Path | None) -> SpecFormat | None:
    if source is None or str(source) == "-":
        return None

    suffix = Path(source).suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    return None


def _parse_json_or_yaml(text: str) -> object:
    try:
        return json.loads(text)
    except JSONDecodeError:
        return yaml.safe_load(text)


def _validate_json_value(value: object) -> JsonConfigValue:
    if value is None or isinstance(value, str | int | bool):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON values cannot be NaN or infinite")
        return value

    if isinstance(value, list):
        return [_validate_json_value(item) for item in value]

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            result[key] = _validate_json_value(item)
        return result

    raise ValueError(f"{type(value).__name__} is not JSON-compatible")
