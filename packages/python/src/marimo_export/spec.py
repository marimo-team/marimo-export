from __future__ import annotations

import ast
import keyword
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SkipValidation,
    StringConstraints,
    ValidationError,
    field_validator,
)
from pydantic.json_schema import GenerateJsonSchema
from typing_extensions import TypeAliasType
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
from marimo_export._python import validate_import_reference
from marimo_export.errors import SpecError
from marimo_export.exporters._registry import _builtin_exporter, _normalize_options

SPEC_SCHEMA = "marimo-export.spec.v1"
_MAX_SAFE_INTEGER = 2**53 - 1
_PYTHON_WHITESPACE = (
    r"\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680"
    r"\u2000-\u200a\u2028\u2029\u202f\u205f\u3000"
)
_UNICODE_SCALAR_LOOKAHEAD = r"(?![\s\S]*[\uD800-\uDFFF])"
_TRUE_END = r"(?![\s\S])"
_UNICODE_STRING_SCHEMA = rf"^{_UNICODE_SCALAR_LOOKAHEAD}[\s\S]*{_TRUE_END}"
_PUBLIC_NAME_SCHEMA = (
    rf"^{_UNICODE_SCALAR_LOOKAHEAD}(?![\s\S]*[\u0000-\u001f\u007f])"
    rf"(?![{_PYTHON_WHITESPACE}])"
    rf"(?![\s\S]*[{_PYTHON_WHITESPACE}]{_TRUE_END})[\s\S]+{_TRUE_END}"
)
_IDENTIFIER_TOKEN_SCHEMA = r"(?:[A-Za-z_]|[^\u0000-\u007f])(?:[A-Za-z0-9_]|[^\u0000-\u007f])*"
_PYTHON_IDENTIFIER_SCHEMA = (
    rf"^{_UNICODE_SCALAR_LOOKAHEAD}(?![\s\S]*[{_PYTHON_WHITESPACE}])"
    rf"{_IDENTIFIER_TOKEN_SCHEMA}{_TRUE_END}"
)
_IMPORT_REFERENCE_SCHEMA = (
    rf"^{_UNICODE_SCALAR_LOOKAHEAD}(?![\s\S]*[{_PYTHON_WHITESPACE}])"
    rf"{_IDENTIFIER_TOKEN_SCHEMA}(?:\.{_IDENTIFIER_TOKEN_SCHEMA})*:"
    rf"{_IDENTIFIER_TOKEN_SCHEMA}(?:\.{_IDENTIFIER_TOKEN_SCHEMA})*{_TRUE_END}"
)
_SAFE_ERROR_PART = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_VALIDATION_ERRORS = 8
_MAX_ERROR_PATH_LENGTH = 512
_MAX_ERROR_MESSAGE_LENGTH = 2_048
_MAX_SPEC_BYTES = 16 * 1024 * 1024
_MAX_YAML_DEPTH = 256
_MAX_YAML_NODES = 100_000
_SCHEMA_NAMES = {
    "_UnicodeStringWire": "unicode_string",
    "_PublicNameWire": "public_name",
    "_PythonIdentifierWire": "python_identifier",
    "_ImportReferenceWire": "import_reference",
    "_JsonValueWire": "json",
    "_JsonObjectWire": "json_object",
    "_ControlsWire": "controls",
    "_ExpressionSourceWire": "expression_source",
    "_CellSourceWire": "cell_source",
    "_SourceWire": "source",
    "_ImportExporterWire": "import_exporter",
    "_VariableExporterWire": "variable_exporter",
    "_ExporterWire": "exporter",
    "_FormatWire": "format",
    "_OutputWire": "output",
}


def _validate_public_name(value: object) -> object:
    if not isinstance(value, str):
        return value
    if (
        not value
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("must be a non-empty string without surrounding whitespace")
    return value


def _validate_python_identifier(value: object) -> object:
    if not isinstance(value, str):
        return value
    value = _validate_public_name(value)
    assert isinstance(value, str)
    if not value.isidentifier() or keyword.iskeyword(value):
        raise ValueError("must be a Python identifier")
    return value


def _validate_import_reference(value: object) -> object:
    if not isinstance(value, str):
        return value
    return validate_import_reference(value, "value")


_UnicodeStringWire = TypeAliasType(
    "_UnicodeStringWire",
    Annotated[
        str,
        StringConstraints(strict=True, pattern=_UNICODE_STRING_SCHEMA),
    ],
)
_PublicNameWire = TypeAliasType(
    "_PublicNameWire",
    Annotated[
        str,
        StringConstraints(strict=True, pattern=_PUBLIC_NAME_SCHEMA),
        BeforeValidator(_validate_public_name),
    ],
)
_PythonIdentifierWire = TypeAliasType(
    "_PythonIdentifierWire",
    Annotated[
        str,
        StringConstraints(strict=True, pattern=_PYTHON_IDENTIFIER_SCHEMA),
        BeforeValidator(_validate_python_identifier),
    ],
)
_ImportReferenceWire = TypeAliasType(
    "_ImportReferenceWire",
    Annotated[
        str,
        StringConstraints(strict=True, pattern=_IMPORT_REFERENCE_SCHEMA),
        BeforeValidator(_validate_import_reference),
    ],
)
_SafeIntegerWire = Annotated[
    int,
    Field(strict=True, ge=-_MAX_SAFE_INTEGER, le=_MAX_SAFE_INTEGER),
]
_NonIntegerNumberWire = Annotated[
    float,
    Field(
        strict=True,
        allow_inf_nan=False,
        ge=-_MAX_SAFE_INTEGER,
        le=_MAX_SAFE_INTEGER,
    ),
]
_JsonValueWire = TypeAliasType(
    "_JsonValueWire",
    _UnicodeStringWire
    | _SafeIntegerWire
    | _NonIntegerNumberWire
    | bool
    | None
    | list["_JsonValueWire"]
    | dict[_UnicodeStringWire, SkipValidation["_JsonValueWire"]],
)
_JsonObjectWire = TypeAliasType(
    "_JsonObjectWire",
    dict[_UnicodeStringWire, SkipValidation[_JsonValueWire]],
)
_ControlsWire = TypeAliasType(
    "_ControlsWire",
    dict[_PythonIdentifierWire, SkipValidation[_JsonValueWire]],
)


class _WireModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        regex_engine="python-re",
        strict=True,
        validate_default=True,
    )


class _ExpressionSourceWireModel(_WireModel):
    model_config = ConfigDict(title="expression source")

    expression: _PublicNameWire

    @field_validator("expression")
    @classmethod
    def _valid_expression(cls, value: str) -> str:
        try:
            ast.parse(value, mode="eval")
        except SyntaxError as error:
            raise ValueError("must be a valid Python expression") from error
        return value


class _CellSourceWireModel(_WireModel):
    model_config = ConfigDict(title="cell source")

    cell: _PublicNameWire


_ExpressionSourceWire = TypeAliasType(
    "_ExpressionSourceWire",
    _ExpressionSourceWireModel,
)
_CellSourceWire = TypeAliasType(
    "_CellSourceWire",
    _CellSourceWireModel,
)
_SourceWire = TypeAliasType(
    "_SourceWire",
    _PythonIdentifierWire | _ExpressionSourceWire | _CellSourceWire,
)


class _ImportExporterWireModel(_WireModel):
    model_config = ConfigDict(title="import exporter")

    import_: _ImportReferenceWire = Field(alias="import")
    version: _PublicNameWire


class _VariableExporterWireModel(_WireModel):
    model_config = ConfigDict(title="variable exporter")

    variable: _PythonIdentifierWire
    version: _PublicNameWire


_ImportExporterWire = TypeAliasType(
    "_ImportExporterWire",
    _ImportExporterWireModel,
)
_VariableExporterWire = TypeAliasType(
    "_VariableExporterWire",
    _VariableExporterWireModel,
)
_ExporterWire = TypeAliasType(
    "_ExporterWire",
    _PublicNameWire | _ImportExporterWire | _VariableExporterWire,
)


class _FormatWireModel(_WireModel):
    model_config = ConfigDict(title="format")

    exporter: _ExporterWire = Field(default_factory=str, validate_default=False)
    options: _JsonObjectWire = Field(default_factory=dict)


_FormatWire = TypeAliasType(
    "_FormatWire",
    _FormatWireModel,
)


class _OutputWireModel(_WireModel):
    model_config = ConfigDict(title="output")

    source: _SourceWire
    formats: dict[_PublicNameWire, _FormatWire] = Field(min_length=1)


_OutputWire = TypeAliasType(
    "_OutputWire",
    _OutputWireModel,
)


class _SpecWire(_WireModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
        title="marimo-export capture specification",
        json_schema_extra={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://marimo.io/schemas/marimo-export/spec.v1.json",
        },
    )

    schema_: Literal["marimo-export.spec.v1"] = Field(alias="schema")
    variants: dict[_PublicNameWire, _ControlsWire] = Field(
        default={"current": {}},
        min_length=1,
    )
    outputs: dict[_PublicNameWire, _OutputWire] = Field(min_length=1)


class _SpecSchemaGenerator(GenerateJsonSchema):
    def normalize_name(self, name: str) -> str:
        normalized = super().normalize_name(name)
        return _SCHEMA_NAMES.get(normalized, normalized)


@dataclass(frozen=True, slots=True)
class Source:
    kind: Literal["global", "expression", "cell"]
    value: str

    def wire(self) -> JsonValue:
        if self.kind == "global":
            return self.value
        return {self.kind: self.value}


@dataclass(frozen=True, slots=True)
class ExporterSpec:
    kind: Literal["builtin", "import", "variable"]
    reference: str
    version: str | None = None

    def wire(self) -> JsonValue:
        if self.kind == "builtin":
            return self.reference
        result: JsonObject = {self.kind: self.reference}
        if self.version is not None:
            result["version"] = self.version
        return result


@dataclass(frozen=True, slots=True, init=False)
class FormatSpec:
    name: str
    exporter: ExporterSpec
    _options_bytes: bytes = field(repr=False)

    @classmethod
    def _from_decoded(
        cls,
        *,
        name: str,
        exporter: ExporterSpec,
        options: JsonObject,
    ) -> FormatSpec:
        instance = object.__new__(cls)
        object.__setattr__(instance, "name", name)
        object.__setattr__(instance, "exporter", exporter)
        object.__setattr__(instance, "_options_bytes", canonical_bytes(options))
        return instance

    @property
    def options(self) -> JsonObject:
        return decode_json_object(self._options_bytes, f"formats.{self.name}.options")

    def wire(self) -> JsonObject:
        result: JsonObject = {}
        if self.exporter.kind != "builtin" or self.exporter.reference != self.name:
            result["exporter"] = self.exporter.wire()
        if self.options:
            result["options"] = json_object(self.options, f"formats.{self.name}.options")
        return result


@dataclass(frozen=True, slots=True)
class OutputSpec:
    name: str
    source: Source
    formats: tuple[FormatSpec, ...]

    def wire(self) -> JsonObject:
        return {
            "source": self.source.wire(),
            "formats": {item.name: item.wire() for item in self.formats},
        }


@dataclass(frozen=True, slots=True, init=False)
class Variant:
    name: str
    _controls_bytes: bytes = field(repr=False)

    @classmethod
    def _from_decoded(cls, *, name: str, controls: JsonObject) -> Variant:
        instance = object.__new__(cls)
        object.__setattr__(instance, "name", name)
        object.__setattr__(instance, "_controls_bytes", canonical_bytes(controls))
        return instance

    @property
    def controls(self) -> JsonObject:
        return decode_json_object(self._controls_bytes, f"variants.{self.name}")

    def wire(self) -> JsonObject:
        return json_object(self.controls, f"variants.{self.name}")


@dataclass(frozen=True, slots=True, init=False)
class ExportSpec:
    variants: tuple[Variant, ...]
    outputs: tuple[OutputSpec, ...]
    _wire_bytes: bytes = field(repr=False)

    def __init__(self, value: object) -> None:
        decoded = decode_spec(value)
        object.__setattr__(self, "variants", decoded.variants)
        object.__setattr__(self, "outputs", decoded.outputs)
        object.__setattr__(self, "_wire_bytes", decoded._wire_bytes)

    @classmethod
    def _from_decoded(
        cls,
        *,
        variants: tuple[Variant, ...],
        outputs: tuple[OutputSpec, ...],
    ) -> ExportSpec:
        wire: JsonObject = {
            "schema": SPEC_SCHEMA,
            "variants": {item.name: item.wire() for item in variants},
            "outputs": {item.name: item.wire() for item in outputs},
        }
        instance = object.__new__(cls)
        object.__setattr__(instance, "variants", variants)
        object.__setattr__(instance, "outputs", outputs)
        object.__setattr__(instance, "_wire_bytes", canonical_bytes(wire))
        return instance

    @classmethod
    def from_value(cls, value: object) -> ExportSpec:
        return decode_spec(value)

    @classmethod
    def from_file(cls, path: str | Path) -> ExportSpec:
        return load_spec(path)

    def wire(self) -> JsonObject:
        return decode_json_object(self._wire_bytes, "export spec")


def decode_spec(value: object) -> ExportSpec:
    if isinstance(value, ExportSpec):
        return value
    try:
        return _decode_spec(value)
    except SpecError:
        raise
    except (TypeError, ValueError) as error:
        raise SpecError(_safe_diagnostic(error)) from error


def load_spec(path: str | Path) -> ExportSpec:
    try:
        source = Path(path).expanduser()
    except (OSError, RuntimeError, TypeError) as error:
        raise SpecError(
            _safe_diagnostic(
                "could not resolve export spec path ",
                path,
                ": ",
                error,
            )
        ) from error
    try:
        with source.open("rb") as stream:
            data = stream.read(_MAX_SPEC_BYTES + 1)
    except (OSError, OverflowError, UnicodeError, ValueError) as error:
        raise SpecError(
            _safe_diagnostic("could not read export spec ", source, ": ", error)
        ) from error
    if len(data) > _MAX_SPEC_BYTES:
        raise SpecError(
            _safe_diagnostic(
                "export spec ",
                source,
                f" exceeds {_MAX_SPEC_BYTES} bytes",
            )
        )
    try:
        text = data.decode("utf-8")
    except UnicodeError as error:
        raise SpecError(
            _safe_diagnostic("could not read export spec ", source, ": ", error)
        ) from error
    try:
        value = yaml.load(text, Loader=_UniqueKeyLoader)
    except SpecError:
        raise
    except RecursionError as error:
        raise SpecError(
            f"export spec YAML exceeds maximum nesting depth of {_MAX_YAML_DEPTH}"
        ) from error
    except yaml.YAMLError as error:
        problem = getattr(error, "problem", None) or str(error)
        raise SpecError(
            _safe_diagnostic(
                "invalid YAML in export spec ",
                source,
                ": ",
                problem,
            )
        ) from error
    except (OverflowError, ValueError) as error:
        raise SpecError(
            _safe_diagnostic(
                "invalid YAML value in export spec ",
                source,
                ": ",
                error,
            )
        ) from error
    return decode_spec(value)


def _decode_spec(value: object) -> ExportSpec:
    root = json_object(value, "spec")
    try:
        wire = _SpecWire.model_validate(root)
    except ValidationError as error:
        raise SpecError(_validation_message(error)) from error

    variants = tuple(_variant(name, item) for name, item in wire.variants.items())
    outputs = tuple(_output(name, item) for name, item in wire.outputs.items())
    return ExportSpec._from_decoded(variants=variants, outputs=outputs)


def _variant(name: str, controls: _ControlsWire) -> Variant:
    return Variant._from_decoded(
        name=name,
        controls={key: cast(JsonValue, value) for key, value in controls.items()},
    )


def _output(name: str, item: _OutputWireModel) -> OutputSpec:
    source = _source(item.source)
    formats = tuple(
        _format(format_name, spec, output=name) for format_name, spec in item.formats.items()
    )
    return OutputSpec(name=name, source=source, formats=formats)


def _source(value: _SourceWire) -> Source:
    if isinstance(value, str):
        return Source(kind="global", value=value)
    if isinstance(value, _ExpressionSourceWireModel):
        return Source(kind="expression", value=value.expression)
    return Source(kind="cell", value=value.cell)


def _format(name: str, item: _FormatWireModel, *, output: str) -> FormatSpec:
    path = _wire_location(("outputs", output, "formats", name))
    exporter_value = item.exporter if "exporter" in item.model_fields_set else name
    exporter = _exporter(exporter_value)
    options = cast(JsonObject, item.options)
    if exporter.kind == "builtin":
        try:
            _builtin_exporter(exporter.reference)
        except ValueError as error:
            raise ValueError(
                f"unknown built-in exporter: {_error_part(exporter.reference)}"
            ) from error
        options = _normalize_options(exporter.reference, options, f"{path}.options")
    return FormatSpec._from_decoded(name=name, exporter=exporter, options=options)


def _exporter(value: _ExporterWire) -> ExporterSpec:
    if isinstance(value, str):
        return ExporterSpec(kind="builtin", reference=value)
    if isinstance(value, _ImportExporterWireModel):
        return ExporterSpec(
            kind="import",
            reference=value.import_,
            version=value.version,
        )
    return ExporterSpec(
        kind="variable",
        reference=value.variable,
        version=value.version,
    )


def _validation_message(error: ValidationError) -> str:
    messages: list[str] = []
    errors = error.errors(include_url=False, include_context=False, include_input=False)
    for item in errors[:_MAX_VALIDATION_ERRORS]:
        location = _wire_location(item["loc"])
        error_type = item["type"]
        if error_type == "extra_forbidden":
            parent = _wire_location(item["loc"][:-1])
            field = _error_part(item["loc"][-1])
            message = f"{parent} does not accept: {field}"
        elif error_type == "missing":
            parent = _wire_location(item["loc"][:-1])
            field = _error_part(item["loc"][-1])
            message = f"{parent} is missing: {field}"
        elif error_type == "too_short":
            message = f"{location} must contain at least one item"
        else:
            detail = item["msg"].removeprefix("Value error, ")
            message = f"{location}: {detail}"
        if message not in messages:
            messages.append(message)
    if len(errors) > _MAX_VALIDATION_ERRORS:
        messages.append(
            f"spec: {len(errors) - _MAX_VALIDATION_ERRORS} additional validation errors"
        )
    return _safe_diagnostic(". ".join(messages))


def _wire_location(location: tuple[int | str, ...]) -> str:
    branches = {
        "str",
        "_ExpressionSourceWireModel",
        "_CellSourceWireModel",
        "_ImportExporterWireModel",
        "_VariableExporterWireModel",
    }
    path = "spec"
    for part in location:
        if part in branches or (isinstance(part, str) and part.startswith("function-after[")):
            continue
        rendered = _error_part(part)
        path += f"[{rendered}]" if isinstance(part, int) else f".{rendered}"
    return path[:_MAX_ERROR_PATH_LENGTH]


def _error_part(part: int | str) -> str:
    if isinstance(part, int):
        return str(part)
    if _SAFE_ERROR_PART.fullmatch(part) is not None:
        return part
    shortened = part if len(part) <= 80 else f"{part[:77]}..."
    return ascii(shortened)


def _safe_diagnostic(*parts: object) -> str:
    return safe_diagnostic(*parts, maximum_chars=_MAX_ERROR_MESSAGE_LENGTH)


class _UniqueKeyLoader(yaml.SafeLoader):
    def __init__(self, stream: Any) -> None:
        super().__init__(stream)
        self._composition_depth = 0
        self._composition_nodes = 0

    def compose_node(self, parent: Node | None, index: Any) -> Node:
        self._composition_depth += 1
        self._composition_nodes += 1
        try:
            if self._composition_depth > _MAX_YAML_DEPTH:
                raise SpecError(
                    f"export spec YAML exceeds maximum nesting depth of {_MAX_YAML_DEPTH}"
                )
            if self._composition_nodes > _MAX_YAML_NODES:
                raise SpecError(f"export spec YAML exceeds maximum node count of {_MAX_YAML_NODES}")
            try:
                return cast(Node, super().compose_node(parent, index))
            except RecursionError as error:
                raise SpecError(
                    f"export spec YAML exceeds maximum nesting depth of {_MAX_YAML_DEPTH}"
                ) from error
        finally:
            self._composition_depth -= 1


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    loader.flatten_mapping(node)
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as error:
            raise SpecError("export spec object keys must be scalar values") from error
        if duplicate:
            raise SpecError(_safe_diagnostic("export spec contains duplicate key ", repr(key)))
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def spec_json_schema() -> JsonObject:
    """Return the JSON Schema for ``marimo-export.spec.v1``."""

    schema = _SpecWire.model_json_schema(
        by_alias=True,
        schema_generator=_SpecSchemaGenerator,
    )
    return json_object(schema, "spec schema")
