from __future__ import annotations

import math
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Annotated, Literal, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
)
from pydantic.json_schema import GenerateJsonSchema
from typing_extensions import TypeAliasType

from marimo_export._format import (
    FORMAT_ID_SCHEMA_PATTERN,
    MAX_FORMAT_ID_ASCII_BYTES,
    MAX_MEDIA_TYPE_ASCII_BYTES,
    MEDIA_TYPE_SCHEMA_PATTERN,
    validate_format_id,
    validate_media_type,
)
from marimo_export._json import (
    JsonObject,
    JsonValue,
    canonical_bytes,
    decode_json_object,
    json_equal,
    json_object,
    json_string,
)
from marimo_export._portable import (
    ASSET_KEY_SCHEMA_PATTERN,
    MAX_ASSET_KEY_UTF8_BYTES,
    NOTEBOOK_BASENAME_SCHEMA_PATTERN,
    validate_asset_key,
    validate_notebook_basename,
)
from marimo_export.errors import PublicationError

PUBLICATION_SCHEMA = "marimo-export.publication.v1"
ASSET_CODEC = "marimo.blob-asset.msgpack.v1"

_SHA256 = re.compile(r"[0-9a-f]{64}")
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
_SHA256_SCHEMA = rf"^[0-9a-f]{{64}}{_TRUE_END}"


def _wire_unicode_string(value: object) -> object:
    if not isinstance(value, str):
        return value
    return json_string(value, "value")


def _wire_public_name(value: object) -> object:
    if not isinstance(value, str):
        return value
    return _name(value, "value")


def _wire_provenance_filename(value: object) -> object:
    if not isinstance(value, str):
        return value
    return validate_notebook_basename(value, "value")


def _wire_asset_key(value: object) -> object:
    if not isinstance(value, str):
        return value
    return validate_asset_key(value, "value")


def _wire_digest(value: object) -> object:
    if not isinstance(value, str):
        return value
    return _digest(value, "value")


def _wire_format_id(value: object) -> object:
    if not isinstance(value, str):
        return value
    return validate_format_id(value, "value")


def _wire_media_type(value: object) -> object:
    if not isinstance(value, str):
        return value
    return validate_media_type(value, "value")


def _normalize_positive_integer(value: object) -> object:
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return value


_UnicodeStringWire = TypeAliasType(
    "_UnicodeStringWire",
    Annotated[
        str,
        StringConstraints(strict=True, pattern=_UNICODE_STRING_SCHEMA),
        BeforeValidator(_wire_unicode_string),
    ],
)
_PublicNameWire = TypeAliasType(
    "_PublicNameWire",
    Annotated[
        str,
        StringConstraints(strict=True, pattern=_PUBLIC_NAME_SCHEMA),
        BeforeValidator(_wire_public_name),
    ],
)
_ProvenanceFilenameWire = TypeAliasType(
    "_ProvenanceFilenameWire",
    Annotated[
        str,
        StringConstraints(strict=True, pattern=NOTEBOOK_BASENAME_SCHEMA_PATTERN),
        BeforeValidator(_wire_provenance_filename),
    ],
)
_AssetKeyWire = TypeAliasType(
    "_AssetKeyWire",
    Annotated[
        str,
        StringConstraints(
            strict=True,
            pattern=ASSET_KEY_SCHEMA_PATTERN,
            max_length=MAX_ASSET_KEY_UTF8_BYTES,
        ),
        BeforeValidator(_wire_asset_key),
    ],
)
_DigestWire = TypeAliasType(
    "_DigestWire",
    Annotated[
        str,
        StringConstraints(strict=True, pattern=_SHA256_SCHEMA),
        BeforeValidator(_wire_digest),
    ],
)
_FormatIdWire = TypeAliasType(
    "_FormatIdWire",
    Annotated[
        str,
        StringConstraints(
            strict=True,
            pattern=FORMAT_ID_SCHEMA_PATTERN,
            max_length=MAX_FORMAT_ID_ASCII_BYTES,
        ),
        BeforeValidator(_wire_format_id),
    ],
)
_MediaTypeWire = TypeAliasType(
    "_MediaTypeWire",
    Annotated[
        str,
        StringConstraints(
            strict=True,
            pattern=MEDIA_TYPE_SCHEMA_PATTERN,
            max_length=MAX_MEDIA_TYPE_ASCII_BYTES,
        ),
        BeforeValidator(_wire_media_type),
    ],
)
_JsonWireInteger = Annotated[
    int,
    Field(strict=True, ge=-_MAX_SAFE_INTEGER, le=_MAX_SAFE_INTEGER),
]
_JsonWireFloat = Annotated[
    float,
    Field(
        strict=True,
        allow_inf_nan=False,
        ge=-_MAX_SAFE_INTEGER,
        le=_MAX_SAFE_INTEGER,
    ),
]
_PositiveInteger = Annotated[
    int,
    Field(strict=True, ge=1, le=_MAX_SAFE_INTEGER),
    BeforeValidator(_normalize_positive_integer),
]
_JsonWireValue = TypeAliasType(
    "_JsonWireValue",
    None
    | bool
    | _UnicodeStringWire
    | _JsonWireInteger
    | _JsonWireFloat
    | list["_JsonWireValue"]
    | dict[_UnicodeStringWire, "_JsonWireValue"],
)


class _WireModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        regex_engine="python-re",
        strict=True,
    )


class _AssetWire(_WireModel):
    model_config = ConfigDict(title="asset")

    key: _AssetKeyWire
    sha256: _DigestWire
    size: _PositiveInteger


class _FormatWire(_WireModel):
    model_config = ConfigDict(title="format")

    format_id: _FormatIdWire
    media_type: _MediaTypeWire
    metadata: dict[_UnicodeStringWire, _JsonWireValue]
    asset: _AssetWire


class _OutputWire(_WireModel):
    model_config = ConfigDict(title="output")

    formats: Annotated[dict[_PublicNameWire, _FormatWire], Field(min_length=1)]


class _VariantWire(_WireModel):
    model_config = ConfigDict(title="variant")

    controls: dict[_PublicNameWire, _JsonWireValue]
    outputs: Annotated[dict[_PublicNameWire, _OutputWire], Field(min_length=1)]


class _NotebookWire(_WireModel):
    model_config = ConfigDict(title="notebook")

    filename: _ProvenanceFilenameWire
    document_sha256: _DigestWire


class _ProducerWire(_WireModel):
    model_config = ConfigDict(title="producer")

    marimo: _PublicNameWire
    marimo_export: _PublicNameWire


class _PublicationWire(_WireModel):
    model_config = ConfigDict(
        title="marimo-export publication index",
        json_schema_extra={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://marimo.io/schemas/marimo-export/publication.v1.json",
        },
    )

    schema_: Literal["marimo-export.publication.v1"] = Field(alias="schema")
    asset_codec: Literal["marimo.blob-asset.msgpack.v1"]
    notebook: _NotebookWire
    producer: _ProducerWire
    variants: Annotated[dict[_PublicNameWire, _VariantWire], Field(min_length=1)]


@dataclass(frozen=True, slots=True)
class AssetRef:
    key: str
    sha256: str
    size: int

    def __post_init__(self) -> None:
        validate_asset_key(self.key, "asset.key")
        _digest(self.sha256, "asset.sha256")
        _positive_integer(self.size, "asset.size")

    def wire(self) -> JsonObject:
        return {"key": self.key, "sha256": self.sha256, "size": self.size}


@dataclass(frozen=True, slots=True, init=False)
class FormatEntry:
    format_id: str
    media_type: str
    _metadata_bytes: bytes = field(repr=False)
    asset: AssetRef

    def __init__(
        self,
        *,
        format_id: str,
        media_type: str,
        metadata: Mapping[str, JsonValue],
        asset: AssetRef,
    ) -> None:
        format_id = validate_format_id(format_id, "format.format_id")
        media_type = validate_media_type(media_type, "format.media_type")
        metadata_bytes = canonical_bytes(json_object(metadata, "format.metadata"))
        if not isinstance(asset, AssetRef):
            raise TypeError("format.asset must be an AssetRef")
        object.__setattr__(self, "format_id", format_id)
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(self, "_metadata_bytes", metadata_bytes)
        object.__setattr__(self, "asset", asset)

    @property
    def metadata(self) -> JsonObject:
        return decode_json_object(self._metadata_bytes, "format.metadata")

    def wire(self) -> JsonObject:
        return {
            "format_id": self.format_id,
            "media_type": self.media_type,
            "metadata": json_object(self.metadata, "format.metadata"),
            "asset": self.asset.wire(),
        }


@dataclass(frozen=True, slots=True)
class OutputEntry:
    formats: Mapping[str, FormatEntry]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "formats",
            MappingProxyType(_entry_mapping(self.formats, FormatEntry, "output.formats")),
        )

    def wire(self) -> JsonObject:
        return {"formats": {name: entry.wire() for name, entry in self.formats.items()}}


@dataclass(frozen=True, slots=True, init=False)
class VariantEntry:
    _controls_bytes: bytes = field(repr=False)
    outputs: Mapping[str, OutputEntry]

    def __init__(
        self,
        *,
        controls: Mapping[str, JsonValue],
        outputs: Mapping[str, OutputEntry],
    ) -> None:
        parsed_controls = {
            _name(name, "variant.controls key"): value
            for name, value in json_object(controls, "variant.controls").items()
        }
        object.__setattr__(self, "_controls_bytes", canonical_bytes(parsed_controls))
        object.__setattr__(
            self,
            "outputs",
            MappingProxyType(_entry_mapping(outputs, OutputEntry, "variant.outputs")),
        )

    @property
    def controls(self) -> JsonObject:
        return decode_json_object(self._controls_bytes, "variant.controls")

    def wire(self) -> JsonObject:
        return {
            "controls": json_object(self.controls, "variant.controls"),
            "outputs": {name: entry.wire() for name, entry in self.outputs.items()},
        }


@dataclass(frozen=True, slots=True)
class NotebookProvenance:
    filename: str
    document_sha256: str

    def __post_init__(self) -> None:
        validate_notebook_basename(self.filename, "notebook.filename")
        _digest(self.document_sha256, "notebook.document_sha256")

    def wire(self) -> JsonObject:
        return {"filename": self.filename, "document_sha256": self.document_sha256}


@dataclass(frozen=True, slots=True)
class ProducerProvenance:
    marimo: str
    marimo_export: str

    def __post_init__(self) -> None:
        _name(self.marimo, "producer.marimo")
        _name(self.marimo_export, "producer.marimo_export")

    def wire(self) -> JsonObject:
        return {"marimo": self.marimo, "marimo_export": self.marimo_export}


@dataclass(frozen=True, slots=True)
class PublicationIndex:
    notebook: NotebookProvenance
    producer: ProducerProvenance
    variants: Mapping[str, VariantEntry]

    def __post_init__(self) -> None:
        if not isinstance(self.notebook, NotebookProvenance):
            raise TypeError("publication.notebook must be NotebookProvenance")
        if not isinstance(self.producer, ProducerProvenance):
            raise TypeError("publication.producer must be ProducerProvenance")
        object.__setattr__(
            self,
            "variants",
            MappingProxyType(_entry_mapping(self.variants, VariantEntry, "publication.variants")),
        )
        self.assets()

    def wire(self) -> JsonObject:
        return {
            "schema": PUBLICATION_SCHEMA,
            "asset_codec": ASSET_CODEC,
            "notebook": self.notebook.wire(),
            "producer": self.producer.wire(),
            "variants": {name: entry.wire() for name, entry in self.variants.items()},
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.wire())

    @classmethod
    def from_wire(cls, value: object) -> PublicationIndex:
        try:
            wire = _PublicationWire.model_validate(json_object(value, "publication"))
            return cls._from_wire_model(wire)
        except PublicationError:
            raise
        except ValidationError as error:
            message = _validation_message(error)
            raise PublicationError(f"invalid publication index: {message}") from error
        except (TypeError, ValueError) as error:
            message = _safe_error_text(str(error))
            raise PublicationError(f"invalid publication index: {message}") from error

    @classmethod
    def _from_wire_model(cls, wire: _PublicationWire) -> PublicationIndex:
        return cls(
            notebook=NotebookProvenance(
                filename=wire.notebook.filename,
                document_sha256=wire.notebook.document_sha256,
            ),
            producer=ProducerProvenance(
                marimo=wire.producer.marimo,
                marimo_export=wire.producer.marimo_export,
            ),
            variants={
                variant_name: VariantEntry(
                    controls=cast(Mapping[str, JsonValue], variant.controls),
                    outputs={
                        output_name: OutputEntry(
                            formats={
                                format_name: FormatEntry(
                                    format_id=format_entry.format_id,
                                    media_type=format_entry.media_type,
                                    metadata=cast(
                                        Mapping[str, JsonValue],
                                        format_entry.metadata,
                                    ),
                                    asset=AssetRef(
                                        key=format_entry.asset.key,
                                        sha256=format_entry.asset.sha256,
                                        size=format_entry.asset.size,
                                    ),
                                )
                                for format_name, format_entry in output.formats.items()
                            }
                        )
                        for output_name, output in variant.outputs.items()
                    },
                )
                for variant_name, variant in wire.variants.items()
            },
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> PublicationIndex:
        try:
            root = decode_json_object(data, "publication")
        except (TypeError, ValueError) as error:
            message = _safe_error_text(str(error))
            raise PublicationError(f"invalid publication index: {message}") from error
        return cls.from_wire(root)

    def assets(self) -> tuple[AssetRef, ...]:
        """Return the unique asset closure in cache-key order."""

        assets: dict[str, AssetRef] = {}
        contracts: dict[str, tuple[str, str, JsonObject]] = {}
        for _, _, _, entry in self.format_entries():
            existing = assets.setdefault(entry.asset.key, entry.asset)
            if existing != entry.asset:
                raise ValueError(
                    f"conflicting asset reference for {_bounded_repr(entry.asset.key)}"
                )
            contract = (
                entry.format_id,
                entry.media_type,
                json_object(entry.metadata, "format.metadata"),
            )
            existing_contract = contracts.get(entry.asset.key)
            if existing_contract is None:
                contracts[entry.asset.key] = contract
            elif existing_contract[:2] != contract[:2] or not json_equal(
                existing_contract[2], contract[2]
            ):
                raise ValueError(
                    f"conflicting format contract for asset {_bounded_repr(entry.asset.key)}"
                )
        return tuple(assets[key] for key in sorted(assets))

    def format_entries(self) -> Iterator[tuple[str, str, str, FormatEntry]]:
        for variant_name, variant in self.variants.items():
            for output_name, output in variant.outputs.items():
                for format_name, entry in output.formats.items():
                    yield variant_name, output_name, format_name, entry


def _entry_mapping(
    value: Mapping[str, object],
    entry_type: type[object],
    path: str,
) -> dict[str, object]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError(f"{path} must contain at least one entry")
    result: dict[str, object] = {}
    for name, entry in value.items():
        validated_name = _name(name, f"{path} key")
        if not isinstance(entry, entry_type):
            raise TypeError(f"{path}.{validated_name} must be {entry_type.__name__}")
        result[validated_name] = entry
    return result


def _name(value: object, path: str) -> str:
    result = _string(value, path)
    if result != result.strip() or any(
        ord(character) < 32 or ord(character) == 127 for character in result
    ):
        raise TypeError(f"{path} must not contain surrounding whitespace or control characters")
    return result


def _string(value: object, path: str) -> str:
    value = json_string(value, path)
    if not value:
        raise TypeError(f"{path} must be a non-empty string")
    return value


def _digest(value: object, path: str) -> str:
    digest = _string(value, path)
    if _SHA256.fullmatch(digest) is None:
        raise TypeError(f"{path} must be a lowercase SHA-256 digest")
    return digest


def _positive_integer(value: object, path: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
        or value > _MAX_SAFE_INTEGER
    ):
        raise TypeError(f"{path} must be a positive safe integer")
    return value


_SCHEMA_FIELDS = frozenset(
    {
        "schema",
        "asset_codec",
        "notebook",
        "filename",
        "document_sha256",
        "producer",
        "marimo",
        "marimo_export",
        "variants",
        "controls",
        "outputs",
        "formats",
        "format_id",
        "media_type",
        "metadata",
        "asset",
        "key",
        "sha256",
        "size",
    }
)
_SCHEMA_NAMES = {
    "_UnicodeStringWire": "unicode_string",
    "_PublicNameWire": "public_name",
    "_ProvenanceFilenameWire": "filename",
    "_AssetKeyWire": "asset_key",
    "_DigestWire": "sha256",
    "_FormatIdWire": "format_id",
    "_MediaTypeWire": "media_type",
    "_AssetWire": "asset",
    "_FormatWire": "format",
    "_JsonWireValue": "json_value",
    "_NotebookWire": "notebook",
    "_OutputWire": "output",
    "_ProducerWire": "producer",
    "_VariantWire": "variant",
}


class _PublicationSchemaGenerator(GenerateJsonSchema):
    def normalize_name(self, name: str) -> str:
        normalized = super().normalize_name(name)
        return _SCHEMA_NAMES.get(normalized, normalized)


def _safe_location_segment(value: object) -> str:
    if isinstance(value, int):
        return str(value)
    text = str(value)
    suffix = "..." if len(text) > 96 else ""
    return ascii(text[:96]) + suffix


def _bounded_repr(value: str) -> str:
    suffix = "..." if len(value) > 96 else ""
    return ascii(value[:96]) + suffix


def _validation_path(location: tuple[object, ...]) -> str:
    result = "publication"
    for segment in location:
        if segment == "[key]":
            result += " key"
        elif isinstance(segment, int):
            result += f"[{segment}]"
        elif isinstance(segment, str) and segment in _SCHEMA_FIELDS:
            result += f".{segment}"
        else:
            result += f"[{_safe_location_segment(segment)}]"
        if len(result) > 512:
            return result[:509] + "..."
    return result


def _safe_error_text(message: str) -> str:
    clipped = message[:512]
    result = clipped.encode("unicode_escape", errors="backslashreplace").decode("ascii")
    return result if len(result) <= 512 else result[:509] + "..."


def _validation_message(error: ValidationError) -> str:
    detail = error.errors(include_input=False, include_url=False)[0]
    location = cast(tuple[object, ...], detail.get("loc", ()))
    error_type = detail.get("type")
    if error_type == "extra_forbidden" and location:
        parent = _validation_path(location[:-1])
        return f"{parent} does not accept: {_safe_location_segment(location[-1])}"
    if error_type == "missing" and location:
        parent = _validation_path(location[:-1])
        return f"{parent} is missing: {_safe_location_segment(location[-1])}"
    message = str(detail.get("msg", "validation failed"))
    if message.startswith("Value error, "):
        message = message.removeprefix("Value error, ")
    return f"{_validation_path(location)} {_safe_error_text(message)}"


def publication_json_schema() -> JsonObject:
    """Return the JSON Schema for ``marimo-export.publication.v1``."""

    schema = _PublicationWire.model_json_schema(
        by_alias=True,
        schema_generator=_PublicationSchemaGenerator,
    )
    return json_object(schema, "publication schema")
