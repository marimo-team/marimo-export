from __future__ import annotations

import keyword
import os
from collections.abc import Mapping
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
from marimo_export._selector import ValueSelector
from marimo_export.errors import SpecError
from marimo_export.exporters._spec import ExporterSpec
from marimo_export.wire import FrozenJsonObject, FrozenJsonValue

SPEC_SCHEMA = "marimo-export.spec.v2"

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
_EXPORT_NAME_PATTERN = (
    rf"^{_UNICODE_SCALAR_LOOKAHEAD}(?![\s\S]*[\u0000-\u001f\u007f])[\s\S]+{_TRUE_END}"
)
_IDENTIFIER_COMPONENT = r"(?:[A-Za-z_]|[^\u0000-\u007f])(?:[A-Za-z0-9_]|[^\u0000-\u007f])*"
_IDENTIFIER_PATTERN = rf"^{_UNICODE_SCALAR_LOOKAHEAD}{_IDENTIFIER_COMPONENT}{_TRUE_END}"
_MODULE_NAME_PATTERN = (
    rf"^{_UNICODE_SCALAR_LOOKAHEAD}{_IDENTIFIER_COMPONENT}"
    rf"(?:\.{_IDENTIFIER_COMPONENT})*{_TRUE_END}"
)


def _validate_export_name(value: object) -> object:
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


def _validate_module_name(value: object) -> object:
    if not isinstance(value, str):
        return value
    if len(value.encode("utf-8")) > _MAX_NAME_BYTES or any(
        not part.isidentifier() or keyword.iskeyword(part) for part in value.split(".")
    ):
        raise ValueError("must be an importable module name of at most 255 UTF-8 bytes")
    return value


_UnicodeStringWire = TypeAliasType(
    "_UnicodeStringWire",
    Annotated[
        str,
        StringConstraints(strict=True, pattern=_UNICODE_STRING_PATTERN),
    ],
)
_ExportNameWire = TypeAliasType(
    "_ExportNameWire",
    Annotated[
        str,
        StringConstraints(strict=True, pattern=_EXPORT_NAME_PATTERN),
        BeforeValidator(_validate_export_name),
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
_ModuleNameWire = TypeAliasType(
    "_ModuleNameWire",
    Annotated[
        str,
        StringConstraints(strict=True, pattern=_MODULE_NAME_PATTERN),
        BeforeValidator(_validate_module_name),
    ],
)
_SafeIntegerWire = Annotated[
    int,
    Field(strict=True, ge=-_MAX_SAFE_INTEGER, le=_MAX_SAFE_INTEGER),
]
_FiniteNumberWire = Annotated[
    float,
    Field(
        strict=True,
        allow_inf_nan=False,
        ge=-_MAX_SAFE_INTEGER,
        le=_MAX_SAFE_INTEGER,
    ),
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


class _ExporterWire(_WireModel):
    model_config = ConfigDict(title="exporter")

    name: _UnicodeStringWire
    options: dict[_IdentifierWire, _PortableValueWire]
    dependencies: list[_ModuleNameWire] = Field(max_length=256)

    @field_validator("dependencies")
    @classmethod
    def _sorted_unique_dependencies(cls, values: list[str]) -> list[str]:
        if values != sorted(set(values)):
            raise ValueError("must contain sorted unique module names")
        return values


class _JsonSourceWire(_WireModel):
    kind: Literal["json"]
    selector: _UnicodeStringWire


class _NativeSourceWire(_WireModel):
    kind: Literal["native"]
    selector: _UnicodeStringWire


class _ExportSourceWire(_WireModel):
    kind: Literal["export"]
    selector: _UnicodeStringWire


class _RenderedOutputSourceWire(_WireModel):
    kind: Literal["output"]
    selector: _UnicodeStringWire


class _CellSourceWire(_WireModel):
    kind: Literal["cell"]
    by: Literal["name", "id"]
    value: _UnicodeStringWire


_OutputSourceWire = Annotated[
    _JsonSourceWire
    | _NativeSourceWire
    | _ExportSourceWire
    | _RenderedOutputSourceWire
    | _CellSourceWire,
    Field(discriminator="kind"),
]


class _OutputWire(_WireModel):
    model_config = ConfigDict(title="output")

    source: _OutputSourceWire
    exporter: _UnicodeStringWire | _ExporterWire | None = None

    @model_validator(mode="after")
    def _exporter_matches_source(self) -> _OutputWire:
        if (self.exporter is not None) != (self.source.kind == "export"):
            raise ValueError("export sources require exactly one exporter")
        return self


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
            "$id": "https://marimo.io/schemas/marimo-export/spec.v2.json",
        },
    )

    schema_: Literal["marimo-export.spec.v2"] = Field(alias="schema")
    default_state: _ExportNameWire
    states: dict[_ExportNameWire, dict[_IdentifierWire, _PortableValueWire]] = Field(min_length=1)
    outputs: dict[_ExportNameWire, _OutputWire] = Field(min_length=1)

    @model_validator(mode="after")
    def _default_state_exists(self) -> _SpecWire:
        if self.default_state not in self.states:
            raise ValueError("default_state must name a declared state")
        return self


class _SpecSchemaGenerator(GenerateJsonSchema):
    _NAMES: ClassVar[dict[str, str]] = {
        "_UnicodeStringWire": "unicode_string",
        "_ExportNameWire": "export_name",
        "_IdentifierWire": "python_identifier",
        "_ModuleNameWire": "python_module_name",
        "_PortableValueWire": "portable_input_value",
        "_JsonSourceWire": "json_source",
        "_NativeSourceWire": "native_source",
        "_ExportSourceWire": "export_source",
        "_RenderedOutputSourceWire": "rendered_output_source",
        "_CellSourceWire": "cell_source",
        "_OutputWire": "output",
        "_ExporterWire": "exporter",
    }

    def normalize_name(self, name: str) -> str:
        normalized = super().normalize_name(name)
        return self._NAMES.get(normalized, normalized)


@dataclass(frozen=True, slots=True)
class JsonSource:
    kind: Literal["json"]
    selector: ValueSelector


@dataclass(frozen=True, slots=True)
class NativeSource:
    kind: Literal["native"]
    selector: ValueSelector


@dataclass(frozen=True, slots=True)
class ExportSource:
    kind: Literal["export"]
    selector: ValueSelector


@dataclass(frozen=True, slots=True)
class RenderedOutputSource:
    kind: Literal["output"]
    selector: ValueSelector


@dataclass(frozen=True, slots=True)
class CellSource:
    kind: Literal["cell"]
    by: Literal["name", "id"]
    value: str


OutputSource: TypeAlias = (
    JsonSource | NativeSource | ExportSource | RenderedOutputSource | CellSource
)


@dataclass(frozen=True, slots=True)
class OutputSpec:
    """Select a value, rendered output, or complete cell."""

    source: OutputSource
    exporter: ExporterSpec | None = None

    def __post_init__(self) -> None:
        if not isinstance(
            self.source,
            (JsonSource, NativeSource, ExportSource, RenderedOutputSource, CellSource),
        ):
            raise TypeError("source must be a JSON, native, export, output, or cell source")
        if isinstance(
            self.source,
            (JsonSource, NativeSource, ExportSource, RenderedOutputSource),
        ) and not isinstance(
            self.source.selector,
            ValueSelector,
        ):
            raise TypeError("selected sources require a ValueSelector")
        if isinstance(self.source, CellSource):
            if self.source.by not in {"name", "id"}:
                raise ValueError("cell source by must be name or id")
            _validate_cell_value(self.source.value, self.source.by)
        if self.exporter is not None and not isinstance(self.exporter, ExporterSpec):
            raise TypeError("exporter must be an ExporterSpec or None")
        if (self.exporter is not None) != isinstance(self.source, ExportSource):
            raise SpecError(
                "export sources require exactly one exporter",
                code="spec_output_invalid",
            )

    @classmethod
    def json(cls, selector: str) -> OutputSpec:
        """Select one canonical portable JSON value."""

        return cls(
            source=JsonSource(kind="json", selector=ValueSelector.parse(selector)),
        )

    @classmethod
    def native(cls, selector: str) -> OutputSpec:
        """Select one cache-native scalar, JSON value, array, table, or blob."""

        return cls(
            source=NativeSource(kind="native", selector=ValueSelector.parse(selector)),
        )

    @classmethod
    def export(cls, selector: str, exporter: ExporterSpec) -> OutputSpec:
        """Convert one selected value through an explicit exporter."""

        return cls(
            source=ExportSource(kind="export", selector=ValueSelector.parse(selector)),
            exporter=exporter,
        )

    @classmethod
    def output(cls, selector: str) -> OutputSpec:
        """Format a selected value through Marimo's output registry."""

        return cls(
            source=RenderedOutputSource(
                kind="output",
                selector=ValueSelector.parse(selector),
            )
        )

    @classmethod
    def cell(
        cls,
        name: str | None = None,
        *,
        id: str | None = None,
    ) -> OutputSpec:
        """Select one complete native cell by name or runtime ID."""

        if (name is None) == (id is None):
            raise TypeError("cell source requires exactly one of name or id")
        by = "name" if name is not None else "id"
        value = name if name is not None else id
        assert value is not None
        _validate_cell_value(value, by)
        return cls(source=CellSource(kind="cell", by=by, value=value))


@dataclass(frozen=True, slots=True, init=False)
class ExportSpec:
    """Declare named states and the notebook definitions to export."""

    default_state: str
    states: Mapping[str, FrozenJsonObject]
    outputs: Mapping[str, OutputSpec]
    _wire_bytes: bytes = field(repr=False)

    def __init__(
        self,
        *,
        default_state: str,
        states: Mapping[str, Mapping[str, JsonValue]],
        outputs: Mapping[str, OutputSpec],
    ) -> None:
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
            output_value: JsonObject = {"source": _source_to_value(output.source)}
            if output.exporter is not None:
                output_value["exporter"] = output.exporter.to_value()
            output_values[name] = output_value
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
                "default_state": default_state,
                "states": state_values,
                "outputs": output_values,
            }
        )
        object.__setattr__(self, "default_state", decoded.default_state)
        object.__setattr__(self, "states", decoded.states)
        object.__setattr__(self, "outputs", decoded.outputs)
        object.__setattr__(self, "_wire_bytes", decoded._wire_bytes)

    @classmethod
    def _create(cls, wire: _SpecWire) -> ExportSpec:
        outputs: JsonObject = {}
        parsed_outputs: dict[str, OutputSpec] = {}
        for name in sorted(wire.outputs):
            output = wire.outputs[name]
            exporter = (
                None
                if output.exporter is None
                else ExporterSpec.from_value(
                    output.exporter.model_dump()
                    if isinstance(output.exporter, _ExporterWire)
                    else output.exporter
                )
            )
            parsed = OutputSpec(source=_source_from_wire(output.source), exporter=exporter)
            parsed_outputs[name] = parsed
            output_value: JsonObject = {"source": _source_to_value(parsed.source)}
            if parsed.exporter is not None:
                output_value["exporter"] = parsed.exporter.to_value()
            outputs[name] = output_value
        value: JsonObject = {
            "schema": SPEC_SCHEMA,
            "default_state": wire.default_state,
            "states": {
                name: cast(JsonValue, dict(wire.states[name])) for name in sorted(wire.states)
            },
            "outputs": outputs,
        }
        instance = object.__new__(cls)
        object.__setattr__(instance, "default_state", wire.default_state)
        object.__setattr__(
            instance,
            "states",
            MappingProxyType(
                {
                    name: cast(
                        FrozenJsonObject,
                        _freeze(cast(JsonObject, dict(wire.states[name]))),
                    )
                    for name in sorted(wire.states)
                }
            ),
        )
        object.__setattr__(
            instance,
            "outputs",
            MappingProxyType(parsed_outputs),
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


def _validate_cell_value(value: object, by: object) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise SpecError(
            f"invalid cell {by} {value!r}: must be a non-empty string "
            "without surrounding whitespace",
            code="spec_output_invalid",
        )
    if len(value.encode("utf-8")) > _MAX_NAME_BYTES:
        raise SpecError(
            f"invalid cell {by} {value!r}: must contain at most {_MAX_NAME_BYTES} UTF-8 bytes",
            code="spec_output_invalid",
        )


def _freeze(value: JsonValue) -> FrozenJsonValue:
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    return value


def _source_from_wire(source: _OutputSourceWire) -> OutputSource:
    if isinstance(source, _JsonSourceWire):
        return JsonSource(kind="json", selector=ValueSelector.parse(source.selector))
    if isinstance(source, _NativeSourceWire):
        return NativeSource(kind="native", selector=ValueSelector.parse(source.selector))
    if isinstance(source, _ExportSourceWire):
        return ExportSource(kind="export", selector=ValueSelector.parse(source.selector))
    if isinstance(source, _RenderedOutputSourceWire):
        return RenderedOutputSource(
            kind="output",
            selector=ValueSelector.parse(source.selector),
        )
    return CellSource(kind="cell", by=source.by, value=source.value)


def _source_to_value(source: OutputSource) -> JsonObject:
    if isinstance(source, JsonSource):
        return {"kind": "json", "selector": source.selector.source}
    if isinstance(source, NativeSource):
        return {"kind": "native", "selector": source.selector.source}
    if isinstance(source, ExportSource):
        return {"kind": "export", "selector": source.selector.source}
    if isinstance(source, RenderedOutputSource):
        return {"kind": "output", "selector": source.selector.source}
    return {"kind": "cell", "by": source.by, "value": source.value}


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
    locations = [tuple(item.get("loc", ())) for item in items]
    if any("exporter" in location for location in locations):
        return "spec_exporter_invalid"
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
