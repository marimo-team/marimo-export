from __future__ import annotations

import keyword
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, ClassVar, Literal, TypeAlias, cast

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic.json_schema import GenerateJsonSchema
from typing_extensions import TypeAliasType
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, Node
from yaml.resolver import BaseResolver

from marimo_export._diagnostics import safe_diagnostic
from marimo_export._json import (
    JsonObject,
    JsonValue,
    canonical_bytes,
    decode_json_object,
    json_object,
)
from marimo_export.errors import SpecError

SPEC_SCHEMA = "marimo-export.spec.v1"

FrozenJsonPrimitive: TypeAlias = str | int | float | bool | None
FrozenJsonValue: TypeAlias = (
    FrozenJsonPrimitive | tuple["FrozenJsonValue", ...] | Mapping[str, "FrozenJsonValue"]
)
FrozenJsonObject: TypeAlias = Mapping[str, FrozenJsonValue]
StrPath: TypeAlias = str | os.PathLike[str]

_MAX_NAME_BYTES = 255
_MAX_SPEC_BYTES = 16 * 1024 * 1024
_MAX_YAML_DEPTH = 256
_MAX_YAML_NODES = 100_000
_MAX_VALIDATION_ERRORS = 8
_MAX_ERROR_MESSAGE_CHARS = 2_048
_MAX_SAFE_INTEGER = 2**53 - 1
_UNICODE_SCALAR_LOOKAHEAD = r"(?![\s\S]*[\uD800-\uDFFF])"
_TRUE_END = r"(?![\s\S])"
_UNICODE_STRING_PATTERN = rf"^{_UNICODE_SCALAR_LOOKAHEAD}[\s\S]*{_TRUE_END}"
_PUBLIC_NAME_PATTERN = (
    rf"^{_UNICODE_SCALAR_LOOKAHEAD}(?![\s\S]*[\u0000-\u001f\u007f])[\s\S]+{_TRUE_END}"
)
_IDENTIFIER_PATTERN = (
    rf"^{_UNICODE_SCALAR_LOOKAHEAD}(?:[A-Za-z_]|[^\u0000-\u007f])"
    rf"(?:[A-Za-z0-9_]|[^\u0000-\u007f])*{_TRUE_END}"
)


def _validate_public_name(value: object) -> object:
    if not isinstance(value, str):
        return value
    if (
        not value
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("must be a non-empty name without surrounding whitespace or controls")
    if len(value.encode("utf-8")) > _MAX_NAME_BYTES:
        raise ValueError(f"must contain at most {_MAX_NAME_BYTES} UTF-8 bytes")
    return value


def _validate_identifier(value: object) -> object:
    if not isinstance(value, str):
        return value
    if (
        not value.isidentifier()
        or keyword.iskeyword(value)
        or len(value.encode("utf-8")) > _MAX_NAME_BYTES
    ):
        raise ValueError("must be a non-keyword Python identifier of at most 255 UTF-8 bytes")
    return value


_UnicodeStringWire = TypeAliasType(
    "_UnicodeStringWire",
    Annotated[
        str,
        StringConstraints(strict=True, pattern=_UNICODE_STRING_PATTERN),
    ],
)
_PublicNameWire = TypeAliasType(
    "_PublicNameWire",
    Annotated[
        str,
        StringConstraints(strict=True, pattern=_PUBLIC_NAME_PATTERN),
        BeforeValidator(_validate_public_name),
    ],
)
_IdentifierWire = TypeAliasType(
    "_IdentifierWire",
    Annotated[
        str,
        StringConstraints(strict=True, pattern=_IDENTIFIER_PATTERN),
        BeforeValidator(_validate_identifier),
    ],
)
_SafeIntegerWire = Annotated[
    int,
    Field(strict=True, ge=-_MAX_SAFE_INTEGER, le=_MAX_SAFE_INTEGER),
]
_FiniteNumberWire = Annotated[
    float,
    Field(strict=True, allow_inf_nan=False),
]
_PortableValueWire = TypeAliasType(
    "_PortableValueWire",
    None
    | bool
    | _UnicodeStringWire
    | _SafeIntegerWire
    | _FiniteNumberWire
    | list["_PortableValueWire"]
    | dict[_UnicodeStringWire, "_PortableValueWire"],
)


class _WireModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        regex_engine="python-re",
        strict=True,
        validate_default=True,
    )


class _OutputWire(_WireModel):
    model_config = ConfigDict(title="output")

    source: _IdentifierWire


class _SpecWire(_WireModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        regex_engine="python-re",
        strict=True,
        validate_default=True,
        title="marimo-export specification",
        json_schema_extra={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://marimo.io/schemas/marimo-export/spec.v1.json",
        },
    )

    schema_: Literal["marimo-export.spec.v1"] = Field(alias="schema")
    inputs: list[_IdentifierWire]
    states: dict[_PublicNameWire, dict[_IdentifierWire, _PortableValueWire]] = Field(min_length=1)
    outputs: dict[_PublicNameWire, _OutputWire] = Field(min_length=1)

    @field_validator("inputs")
    @classmethod
    def _unique_inputs(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("must contain unique definition names")
        return values

    @model_validator(mode="after")
    def _declared_state_inputs(self) -> _SpecWire:
        declared = set(self.inputs)
        unknown = sorted(
            {name for state in self.states.values() for name in state if name not in declared}
        )
        if unknown:
            raise ValueError(f"state rows name undeclared inputs: {', '.join(unknown)}")
        return self


class _SpecSchemaGenerator(GenerateJsonSchema):
    _NAMES: ClassVar[dict[str, str]] = {
        "_UnicodeStringWire": "unicode_string",
        "_PublicNameWire": "public_name",
        "_IdentifierWire": "python_identifier",
        "_PortableValueWire": "portable_input_value",
        "_OutputWire": "output",
    }

    def normalize_name(self, name: str) -> str:
        normalized = super().normalize_name(name)
        return self._NAMES.get(normalized, normalized)


@dataclass(frozen=True, slots=True)
class OutputSpec:
    """Select one notebook definition as a public output."""

    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, str):
            raise TypeError("source must be a string")
        try:
            _validate_identifier(self.source)
        except ValueError as error:
            raise SpecError(
                f"invalid output source {self.source!r}: {error}",
                code="spec_output_invalid",
            ) from error


@dataclass(frozen=True, slots=True, init=False)
class ExportSpec:
    """Declare a finite state matrix and the definitions to publish."""

    inputs: tuple[str, ...]
    states: Mapping[str, FrozenJsonObject]
    outputs: Mapping[str, OutputSpec]
    _wire_bytes: bytes = field(repr=False)

    def __init__(
        self,
        *,
        inputs: Iterable[str],
        states: Mapping[str, Mapping[str, JsonValue]],
        outputs: Mapping[str, OutputSpec],
    ) -> None:
        if isinstance(inputs, (str, bytes)) or not isinstance(inputs, Iterable):
            raise TypeError("inputs must be an iterable of definition names")
        if not isinstance(states, Mapping):
            raise TypeError("states must be a mapping")
        if not isinstance(outputs, Mapping):
            raise TypeError("outputs must be a mapping")
        output_values: JsonObject = {}
        for name, output in outputs.items():
            if not isinstance(name, str):
                raise TypeError("output names must be strings")
            if not isinstance(output, OutputSpec):
                raise TypeError(f"outputs[{name!r}] must be an OutputSpec")
            output_values[name] = {"source": output.source}
        state_values: JsonObject = {}
        for name, row in states.items():
            if not isinstance(name, str):
                raise TypeError("state names must be strings")
            if not isinstance(row, Mapping):
                raise TypeError(f"states[{name!r}] must be a mapping")
            state_values[name] = cast(JsonValue, dict(row))
        decoded = self._decode(
            {
                "schema": SPEC_SCHEMA,
                "inputs": list(inputs),
                "states": state_values,
                "outputs": output_values,
            }
        )
        object.__setattr__(self, "inputs", decoded.inputs)
        object.__setattr__(self, "states", decoded.states)
        object.__setattr__(self, "outputs", decoded.outputs)
        object.__setattr__(self, "_wire_bytes", decoded._wire_bytes)

    @classmethod
    def _create(cls, wire: _SpecWire) -> ExportSpec:
        value: JsonObject = {
            "schema": SPEC_SCHEMA,
            "inputs": list(wire.inputs),
            "states": {name: cast(JsonValue, dict(row)) for name, row in wire.states.items()},
            "outputs": {name: {"source": output.source} for name, output in wire.outputs.items()},
        }
        instance = object.__new__(cls)
        object.__setattr__(instance, "inputs", tuple(wire.inputs))
        object.__setattr__(
            instance,
            "states",
            MappingProxyType(
                {
                    name: cast(FrozenJsonObject, _freeze(cast(JsonObject, dict(row))))
                    for name, row in wire.states.items()
                }
            ),
        )
        object.__setattr__(
            instance,
            "outputs",
            MappingProxyType(
                {name: OutputSpec(source=output.source) for name, output in wire.outputs.items()}
            ),
        )
        object.__setattr__(instance, "_wire_bytes", canonical_bytes(value))
        return instance

    @classmethod
    def _decode(cls, value: object) -> ExportSpec:
        try:
            root = json_object(value, "spec")
            wire = _SpecWire.model_validate(root)
            return cls._create(wire)
        except SpecError:
            raise
        except ValidationError as error:
            code = _spec_error_code(error)
            raise SpecError(_validation_message(error), code=code) from error
        except (TypeError, ValueError) as error:
            raise SpecError(
                safe_diagnostic(error, maximum_chars=_MAX_ERROR_MESSAGE_CHARS),
                code="spec_value_invalid",
            ) from error

    @classmethod
    def from_value(cls, value: object) -> ExportSpec:
        """Validate the exact wire value."""

        if isinstance(value, ExportSpec):
            return value
        return cls._decode(value)

    @classmethod
    def from_file(cls, path: StrPath) -> ExportSpec:
        """Load a strict UTF-8 JSON or safe YAML specification."""

        source = _spec_path(path)
        data = _read_spec(source)
        suffix = source.suffix.lower()
        if suffix == ".json":
            try:
                value = decode_json_object(data, f"export spec {source}")
            except (TypeError, ValueError) as error:
                raise SpecError(
                    safe_diagnostic(error, maximum_chars=_MAX_ERROR_MESSAGE_CHARS),
                    code="spec_invalid",
                ) from error
        elif suffix in {".yaml", ".yml"}:
            value = _decode_yaml(data, source)
        else:
            raise SpecError(
                f"export spec path must end in .json, .yaml, or .yml: {source}",
                code="spec_invalid",
            )
        return cls.from_value(value)

    @classmethod
    def json_schema(cls) -> JsonObject:
        """Return a detached Draft 2020-12 authoring schema."""

        schema = _SpecWire.model_json_schema(
            by_alias=True,
            schema_generator=_SpecSchemaGenerator,
        )
        return json_object(schema, "export spec schema")

    def to_value(self) -> JsonObject:
        """Return a detached mutable wire value."""

        return decode_json_object(self._wire_bytes, "export spec")


def _freeze(value: JsonValue) -> FrozenJsonValue:
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    return value


def _spec_path(path: StrPath) -> Path:
    if not isinstance(path, (str, os.PathLike)):
        raise TypeError("path must be a string or path-like object")
    try:
        return Path(path)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise SpecError(
            safe_diagnostic(
                "could not resolve export spec path: ",
                error,
                maximum_chars=_MAX_ERROR_MESSAGE_CHARS,
            ),
            code="spec_invalid",
        ) from error


def _read_spec(path: Path) -> bytes:
    try:
        with path.open("rb") as stream:
            data = stream.read(_MAX_SPEC_BYTES + 1)
    except (OSError, OverflowError, ValueError) as error:
        raise SpecError(
            safe_diagnostic(
                "could not read export spec ",
                path,
                ": ",
                error,
                maximum_chars=_MAX_ERROR_MESSAGE_CHARS,
            ),
            code="spec_invalid",
        ) from error
    if len(data) > _MAX_SPEC_BYTES:
        raise SpecError(
            f"export spec exceeds {_MAX_SPEC_BYTES} bytes: {path}",
            code="spec_invalid",
        )
    return data


def _decode_yaml(data: bytes, source: Path) -> object:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SpecError(
            f"export spec must be UTF-8: {source}",
            code="spec_invalid",
        ) from error
    try:
        return yaml.load(text, Loader=_UniqueSafeLoader)
    except SpecError:
        raise
    except RecursionError as error:
        raise SpecError(
            f"export spec YAML exceeds {_MAX_YAML_DEPTH} container levels",
            code="spec_invalid",
        ) from error
    except yaml.YAMLError as error:
        problem = getattr(error, "problem", None) or str(error)
        raise SpecError(
            safe_diagnostic(
                "invalid YAML in export spec ",
                source,
                ": ",
                problem,
                maximum_chars=_MAX_ERROR_MESSAGE_CHARS,
            ),
            code="spec_invalid",
        ) from error


class _UniqueSafeLoader(yaml.SafeLoader):
    def __init__(self, stream: Any) -> None:
        super().__init__(stream)
        self._composition_depth = 0
        self._composition_nodes = 0

    def compose_node(self, parent: Node | None, index: Any) -> Node:
        if self.check_event(AliasEvent):
            raise SpecError("export spec YAML aliases are invalid", code="spec_invalid")
        self._composition_depth += 1
        self._composition_nodes += 1
        try:
            if self._composition_depth > _MAX_YAML_DEPTH:
                raise SpecError(
                    f"export spec YAML exceeds {_MAX_YAML_DEPTH} container levels",
                    code="spec_invalid",
                )
            if self._composition_nodes > _MAX_YAML_NODES:
                raise SpecError(
                    f"export spec YAML exceeds {_MAX_YAML_NODES} nodes",
                    code="spec_invalid",
                )
            return cast(Node, super().compose_node(parent, index))
        finally:
            self._composition_depth -= 1


def _construct_unique_mapping(
    loader: _UniqueSafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge":
            raise SpecError("export spec YAML merge keys are invalid", code="spec_invalid")
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as error:
            raise SpecError(
                "export spec object keys must be scalar values",
                code="spec_invalid",
            ) from error
        if duplicate:
            raise SpecError(
                safe_diagnostic(
                    "export spec contains duplicate key ",
                    repr(key),
                    maximum_chars=_MAX_ERROR_MESSAGE_CHARS,
                ),
                code="spec_invalid",
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueSafeLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _spec_error_code(error: ValidationError) -> str:
    items = error.errors(include_url=False)
    if any("undeclared inputs" in str(item.get("msg", "")) for item in items):
        return "spec_state_input_unknown"
    locations = [tuple(item.get("loc", ())) for item in items]
    if any("states" in location for location in locations):
        return "spec_value_invalid"
    if any("outputs" in location for location in locations):
        return "spec_output_invalid"
    return "spec_invalid"


def _validation_message(error: ValidationError) -> str:
    messages: list[str] = []
    items = error.errors(include_url=False, include_context=False, include_input=False)
    for item in items[:_MAX_VALIDATION_ERRORS]:
        location = _validation_path(cast(tuple[object, ...], item.get("loc", ())))
        kind = item.get("type")
        if kind == "extra_forbidden":
            message = (
                f"{_validation_path(tuple(item['loc'][:-1]))} does not accept {item['loc'][-1]!r}"
            )
        elif kind == "missing":
            message = f"{_validation_path(tuple(item['loc'][:-1]))} is missing {item['loc'][-1]!r}"
        else:
            detail = str(item.get("msg", "validation failed")).removeprefix("Value error, ")
            message = f"{location}: {detail}"
        if message not in messages:
            messages.append(message)
    remaining = len(items) - len(items[:_MAX_VALIDATION_ERRORS])
    if remaining:
        messages.append(f"spec: {remaining} additional validation errors")
    return safe_diagnostic(
        ". ".join(messages),
        maximum_chars=_MAX_ERROR_MESSAGE_CHARS,
    )


def _validation_path(location: tuple[object, ...]) -> str:
    path = "spec"
    for part in location:
        if isinstance(part, str) and part.startswith("function-after["):
            continue
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


__all__ = ["ExportSpec", "FrozenJsonObject", "FrozenJsonValue", "OutputSpec", "StrPath"]
