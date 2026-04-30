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

SpecKey: TypeAlias = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]*$")]
StateTarget: TypeAlias = Annotated[
    str,
    Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$"),
]
JsonConfigValue: TypeAlias = (
    str | int | float | bool | None | list[Any] | dict[str, Any]
)
JsonConfigObject: TypeAlias = dict[str, Any]


class SpecModel(BaseModel):
    """Base model for user-authored export specs.

    Specs are public API. Unknown keys fail loudly instead of being silently
    ignored after a typo.
    """

    model_config = ConfigDict(extra="forbid")


class BundleSpec(SpecModel):
    """Where the materialized static export bundle should be written."""

    path: str = Field(
        description="Filesystem path used by the kernel-side bundle writer.",
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_path_shorthand(cls, value: object) -> object:
        if isinstance(value, str):
            return {"path": value}
        return value

    @field_validator("path")
    @classmethod
    def _path_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("bundle path must not be empty")
        return value


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


class CodeStateValue(SpecModel):
    """Scenario state value computed from a Python expression."""

    type: Literal["code"] = Field(
        description=(
            "Discriminator for a scenario state value computed from Python code."
        ),
    )
    expression: str = Field(
        description=(
            "Python expression evaluated in the live notebook runtime before "
            "calling `mox.evaluate`."
        ),
    )

    @field_validator("expression")
    @classmethod
    def _expression_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("state expression must not be empty")
        return value


def _validate_state_value(value: object) -> object:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        if mapping.get("type") == "code":
            return CodeStateValue.model_validate(dict(mapping))
    return _validate_json_value(value)


StateValue: TypeAlias = CodeStateValue | JsonConfigValue


class FormatSpec(SpecModel):
    """One portable representation to produce for a value."""

    export: ExportCallable = Field(
        description="Callable that projects the live Python value into an artifact.",
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
    """Named Python expression whose result should be exported."""

    source: str = Field(
        description=(
            "Python expression evaluated by `mox.evaluate`, "
            "for example `df` or `df.head(10)`."
        ),
    )
    formats: Annotated[
        dict[SpecKey, FormatSpec],
        Field(
            min_length=1,
            description="Named output formats to produce for this value.",
        ),
    ]

    @field_validator("source")
    @classmethod
    def _source_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value source must not be empty")
        return value


class ScenarioSpec(SpecModel):
    """One named finite notebook state to materialize.

    State keys use native Python assignment shapes. A bare name such as
    ``symbols`` overrides a notebook definition. A dotted path such as
    ``symbols_selector.value`` patches an attribute after the object has been
    materialized. The authored spec has one concept, scenario state; the
    evaluator splits it into execution phases later.
    """

    id: SpecKey = Field(
        default="default",
        description="Stable id used in artifact paths and manifest lookup.",
    )
    state: dict[StateTarget, StateValue] = Field(
        default_factory=dict,
        description=(
            "Finite notebook state for this scenario. Bare names override "
            "notebook definitions; dotted paths patch materialized object "
            "attributes, for example `slider.value`."
        ),
    )

    @field_validator("state", mode="before")
    @classmethod
    def _state_must_be_json_or_code(cls, value: object) -> object:
        return _validate_value_mapping(value, "scenario state")


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


class ExportSpec(SpecModel):
    """Complete declaration of the values and scenarios to export."""

    notebook: str | None = Field(
        default=None,
        description=(
            "Optional producer-facing notebook path. The kernel runner does "
            "not need it once attached to a live session, but an outer producer "
            "usually does."
        ),
    )
    bundle: BundleSpec | None = Field(
        default=None,
        description=(
            "Optional bundle destination. The producer may also provide this "
            "outside the spec, but keeping it here makes standalone specs "
            "ergonomic."
        ),
    )
    scenarios: list[ScenarioSpec] = Field(
        default_factory=lambda: [ScenarioSpec()],
        description="Explicit scenario matrix. Omitted means one default scenario.",
    )
    values: Annotated[
        dict[SpecKey, ValueSpec],
        Field(
            min_length=1,
            description="Named values to evaluate and project into artifact formats.",
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
