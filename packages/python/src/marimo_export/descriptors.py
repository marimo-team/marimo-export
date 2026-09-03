from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, TypeAlias, cast

from marimo_export._format import (
    MAX_PROVENANCE_BYTES as _MAX_PROVENANCE_BYTES,
)
from marimo_export._format import (
    bounded_printable as _bounded_printable,
)
from marimo_export._format import (
    digest as _digest,
)
from marimo_export._format import (
    exact_fields as _exact_fields,
)
from marimo_export._format import (
    object_value as _object,
)
from marimo_export._json import (
    JsonObject,
    JsonValue,
    canonical_bytes,
    decode_json,
    decode_json_object,
    portable_json_object,
)
from marimo_export._media_type import MAX_BLOB_METADATA_JSON_BYTES, validate_media_type
from marimo_export._portable import validate_portable_basename
from marimo_export.wire import FrozenJsonValue, _freeze_json, portable_json

SCALAR_CODEC = "marimo.scalar.v1"
JSON_CODEC = "marimo.json.v1"
MARIMO_OUTPUT_CODEC = "marimo.output.v1"
MARIMO_CELL_CODEC = "marimo.cell.v1"
NUMPY_CODEC = "numpy.npy.v1"
ARROW_CODEC = "apache.arrow.file.v1"
BLOB_ASSET_CODEC = "marimo.blob-asset.msgpack.v1"
SCALAR_MEDIA_TYPE = "application/vnd.marimo.scalar.v1+json"
JSON_MEDIA_TYPE = "application/vnd.marimo.json.v1+json"
MARIMO_OUTPUT_MEDIA_TYPE = "application/vnd.marimo.output.v1+json"
MARIMO_CELL_MEDIA_TYPE = "application/vnd.marimo.cell.v1+json"
NUMPY_MEDIA_TYPE = "application/x-npy"
ARROW_MEDIA_TYPE = "application/vnd.apache.arrow.file"

OutputCodec: TypeAlias = Literal[
    "marimo.scalar.v1",
    "marimo.json.v1",
    "marimo.output.v1",
    "marimo.cell.v1",
    "numpy.npy.v1",
    "apache.arrow.file.v1",
    "marimo.blob-asset.msgpack.v1",
]
ScalarValue: TypeAlias = None | bool | str | int | float
ScalarWireValue: TypeAlias = JsonValue

_ASSET_EXTENSIONS: dict[str, str] = {
    MARIMO_OUTPUT_CODEC: "output.json",
    MARIMO_CELL_CODEC: "cell.json",
    NUMPY_CODEC: "npy",
    ARROW_CODEC: "arrow",
    BLOB_ASSET_CODEC: "bin",
}
_MAX_ASSET_SIZE = 2_147_483_647
_MAX_SAFE_INTEGER = 2**53 - 1
_BIGINT = re.compile(r"(?:0|-[1-9][0-9]*|[1-9][0-9]*)")
_SPECIAL_FLOATS = frozenset({"nan", "infinity", "-infinity", "negative-zero"})
_CODECS = frozenset(
    {
        SCALAR_CODEC,
        JSON_CODEC,
        MARIMO_OUTPUT_CODEC,
        MARIMO_CELL_CODEC,
        NUMPY_CODEC,
        ARROW_CODEC,
        BLOB_ASSET_CODEC,
    }
)


@dataclass(frozen=True, slots=True)
class Provenance:
    python_type: str

    def __post_init__(self) -> None:
        _bounded_printable(
            self.python_type,
            "provenance.python_type",
            _MAX_PROVENANCE_BYTES,
        )

    def to_value(self) -> JsonObject:
        return {"python_type": self.python_type}


@dataclass(frozen=True, slots=True)
class AssetRef:
    sha256: str
    size: int

    def __post_init__(self) -> None:
        _digest(self.sha256, "asset.sha256")
        if (
            not isinstance(self.size, int)
            or isinstance(self.size, bool)
            or not 1 <= self.size <= _MAX_ASSET_SIZE
        ):
            raise ValueError(f"asset.size must be an integer from 1 through {_MAX_ASSET_SIZE}")

    def to_value(self) -> JsonObject:
        return {"sha256": self.sha256, "size": self.size}

    def path(self, codec: OutputCodec) -> str:
        return asset_path(codec, self.sha256)


@dataclass(frozen=True, slots=True)
class ScalarDescriptor:
    value: ScalarValue
    provenance: Provenance
    codec: Literal["marimo.scalar.v1"] = field(default=SCALAR_CODEC, init=False)
    media_type: Literal["application/vnd.marimo.scalar.v1+json"] = field(
        default=SCALAR_MEDIA_TYPE,
        init=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.provenance, Provenance):
            raise TypeError("scalar provenance must be Provenance")
        _scalar_to_wire(self.value)

    def to_value(self) -> JsonObject:
        return {
            "codec": self.codec,
            "media_type": self.media_type,
            "provenance": self.provenance.to_value(),
            "value": _scalar_to_wire(self.value),
        }


@dataclass(frozen=True, slots=True, init=False)
class JsonDescriptor:
    provenance: Provenance
    codec: Literal["marimo.json.v1"] = field(default=JSON_CODEC, init=False)
    media_type: Literal["application/vnd.marimo.json.v1+json"] = field(
        default=JSON_MEDIA_TYPE,
        init=False,
    )
    _value_bytes: bytes = field(repr=False)

    def __init__(self, *, value: JsonValue, provenance: Provenance) -> None:
        if not isinstance(provenance, Provenance):
            raise TypeError("JSON provenance must be Provenance")
        encoded = canonical_bytes(portable_json(value, "JSON output"))
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "codec", JSON_CODEC)
        object.__setattr__(self, "media_type", JSON_MEDIA_TYPE)
        object.__setattr__(self, "_value_bytes", encoded)

    @property
    def value(self) -> FrozenJsonValue:
        return _freeze_json(decode_json(self._value_bytes, "JSON output"))

    def to_value(self) -> JsonObject:
        return {
            "codec": self.codec,
            "media_type": self.media_type,
            "provenance": self.provenance.to_value(),
            "value": decode_json(self._value_bytes, "JSON output"),
        }


@dataclass(frozen=True, slots=True)
class MarimoOutputDescriptor:
    asset: AssetRef
    provenance: Provenance
    codec: Literal["marimo.output.v1"] = field(default=MARIMO_OUTPUT_CODEC, init=False)
    media_type: Literal["application/vnd.marimo.output.v1+json"] = field(
        default=MARIMO_OUTPUT_MEDIA_TYPE,
        init=False,
    )

    def __post_init__(self) -> None:
        _asset_descriptor(self.asset, self.provenance, "Marimo output")

    def to_value(self) -> JsonObject:
        return _asset_descriptor_value(self.codec, self.media_type, self.asset, self.provenance)


@dataclass(frozen=True, slots=True)
class MarimoCellDescriptor:
    asset: AssetRef
    provenance: Provenance
    codec: Literal["marimo.cell.v1"] = field(default=MARIMO_CELL_CODEC, init=False)
    media_type: Literal["application/vnd.marimo.cell.v1+json"] = field(
        default=MARIMO_CELL_MEDIA_TYPE,
        init=False,
    )

    def __post_init__(self) -> None:
        _asset_descriptor(self.asset, self.provenance, "Marimo cell")

    def to_value(self) -> JsonObject:
        return _asset_descriptor_value(self.codec, self.media_type, self.asset, self.provenance)


@dataclass(frozen=True, slots=True)
class NumpyDescriptor:
    asset: AssetRef
    provenance: Provenance
    codec: Literal["numpy.npy.v1"] = field(default=NUMPY_CODEC, init=False)
    media_type: Literal["application/x-npy"] = field(default=NUMPY_MEDIA_TYPE, init=False)

    def __post_init__(self) -> None:
        _asset_descriptor(self.asset, self.provenance, "NumPy")

    def to_value(self) -> JsonObject:
        return _asset_descriptor_value(self.codec, self.media_type, self.asset, self.provenance)


@dataclass(frozen=True, slots=True)
class ArrowDescriptor:
    asset: AssetRef
    provenance: Provenance
    codec: Literal["apache.arrow.file.v1"] = field(default=ARROW_CODEC, init=False)
    media_type: Literal["application/vnd.apache.arrow.file"] = field(
        default=ARROW_MEDIA_TYPE,
        init=False,
    )

    def __post_init__(self) -> None:
        _asset_descriptor(self.asset, self.provenance, "Arrow")

    def to_value(self) -> JsonObject:
        return _asset_descriptor_value(self.codec, self.media_type, self.asset, self.provenance)


@dataclass(frozen=True, slots=True, init=False)
class BlobAssetDescriptor:
    asset: AssetRef
    provenance: Provenance
    media_type: str
    filename: str | None
    _metadata_bytes: bytes = field(repr=False)
    codec: Literal["marimo.blob-asset.msgpack.v1"] = field(
        default=BLOB_ASSET_CODEC,
        init=False,
    )

    def __init__(
        self,
        *,
        asset: AssetRef,
        provenance: Provenance,
        media_type: str,
        filename: str | None,
        metadata: Mapping[str, JsonValue],
    ) -> None:
        _asset_descriptor(asset, provenance, "BlobAsset")
        try:
            validated_media_type = validate_media_type(media_type, "BlobAsset media_type")
        except (TypeError, ValueError) as error:
            raise ValueError("BlobAsset media_type is invalid") from error
        if filename is not None:
            try:
                validate_portable_basename(filename, "BlobAsset filename")
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "BlobAsset filename must be a portable basename or null"
                ) from error
        metadata_bytes = canonical_bytes(portable_json_object(metadata, "BlobAsset metadata"))
        if len(metadata_bytes) > MAX_BLOB_METADATA_JSON_BYTES:
            raise ValueError(
                f"BlobAsset metadata exceeds {MAX_BLOB_METADATA_JSON_BYTES} canonical JSON bytes"
            )
        object.__setattr__(self, "asset", asset)
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "media_type", validated_media_type)
        object.__setattr__(self, "filename", filename)
        object.__setattr__(self, "_metadata_bytes", metadata_bytes)
        object.__setattr__(self, "codec", BLOB_ASSET_CODEC)

    @property
    def metadata(self) -> JsonObject:
        return decode_json_object(self._metadata_bytes, "BlobAsset metadata")

    def to_value(self) -> JsonObject:
        return {
            "asset": self.asset.to_value(),
            "codec": self.codec,
            "filename": self.filename,
            "media_type": self.media_type,
            "metadata": self.metadata,
            "provenance": self.provenance.to_value(),
        }


OutputDescriptor: TypeAlias = (
    ScalarDescriptor
    | JsonDescriptor
    | MarimoOutputDescriptor
    | MarimoCellDescriptor
    | NumpyDescriptor
    | ArrowDescriptor
    | BlobAssetDescriptor
)
InlineDescriptor: TypeAlias = ScalarDescriptor | JsonDescriptor
AssetDescriptor: TypeAlias = (
    MarimoOutputDescriptor
    | MarimoCellDescriptor
    | NumpyDescriptor
    | ArrowDescriptor
    | BlobAssetDescriptor
)


def asset_path(codec: OutputCodec, digest: str) -> str:
    if codec not in _ASSET_EXTENSIONS:
        raise ValueError(f"codec {codec!r} has no asset path")
    validated = _digest(digest, "asset digest")
    return f"assets/{validated}.{_ASSET_EXTENSIONS[codec]}"


def _descriptor(value: object, output_name: str) -> OutputDescriptor:
    path = f"output {output_name!r}"
    item = _object(value, path)
    codec = item.get("codec")
    if codec == SCALAR_CODEC:
        _exact_fields(item, {"codec", "media_type", "provenance", "value"}, path)
        if item["media_type"] != SCALAR_MEDIA_TYPE:
            raise ValueError(f"{path} has an invalid scalar media_type")
        return ScalarDescriptor(
            value=_scalar_from_wire(item["value"]),
            provenance=_provenance(item["provenance"], path),
        )
    if codec == JSON_CODEC:
        _exact_fields(item, {"codec", "media_type", "provenance", "value"}, path)
        if item["media_type"] != JSON_MEDIA_TYPE:
            raise ValueError(f"{path} has an invalid JSON media_type")
        return JsonDescriptor(
            value=cast(JsonValue, item["value"]),
            provenance=_provenance(item["provenance"], path),
        )
    if codec in {MARIMO_OUTPUT_CODEC, MARIMO_CELL_CODEC}:
        _exact_fields(item, {"asset", "codec", "media_type", "provenance"}, path)
        provenance = _provenance(item["provenance"], path)
        asset = _asset(item["asset"], path)
        if codec == MARIMO_OUTPUT_CODEC:
            if item["media_type"] != MARIMO_OUTPUT_MEDIA_TYPE:
                raise ValueError(f"{path} has an invalid Marimo output media_type")
            return MarimoOutputDescriptor(asset=asset, provenance=provenance)
        if item["media_type"] != MARIMO_CELL_MEDIA_TYPE:
            raise ValueError(f"{path} has an invalid Marimo cell media_type")
        return MarimoCellDescriptor(asset=asset, provenance=provenance)
    if codec in {NUMPY_CODEC, ARROW_CODEC}:
        _exact_fields(item, {"asset", "codec", "media_type", "provenance"}, path)
        provenance = _provenance(item["provenance"], path)
        asset = _asset(item["asset"], path)
        if codec == NUMPY_CODEC:
            if item["media_type"] != NUMPY_MEDIA_TYPE:
                raise ValueError(f"{path} has an invalid NumPy media_type")
            return NumpyDescriptor(asset=asset, provenance=provenance)
        if item["media_type"] != ARROW_MEDIA_TYPE:
            raise ValueError(f"{path} has an invalid Arrow media_type")
        return ArrowDescriptor(asset=asset, provenance=provenance)
    if codec == BLOB_ASSET_CODEC:
        _exact_fields(
            item,
            {
                "asset",
                "codec",
                "filename",
                "media_type",
                "metadata",
                "provenance",
            },
            path,
        )
        filename = item["filename"]
        if filename is not None and not isinstance(filename, str):
            raise TypeError(f"{path}.filename must be a string or null")
        return BlobAssetDescriptor(
            asset=_asset(item["asset"], path),
            provenance=_provenance(item["provenance"], path),
            media_type=cast(str, item["media_type"]),
            filename=filename,
            metadata=_object(item["metadata"], f"{path}.metadata"),
        )
    raise ValueError(f"{path}.codec must be one of {sorted(_CODECS)!r}")


def _provenance(value: object, path: str) -> Provenance:
    item = _object(value, f"{path}.provenance")
    _exact_fields(item, {"python_type"}, f"{path}.provenance")
    return Provenance(python_type=cast(str, item["python_type"]))


def _asset(value: object, path: str) -> AssetRef:
    item = _object(value, f"{path}.asset")
    _exact_fields(item, {"sha256", "size"}, f"{path}.asset")
    return AssetRef(sha256=cast(str, item["sha256"]), size=cast(int, item["size"]))


def _scalar_to_wire(value: ScalarValue) -> ScalarWireValue:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        if abs(value) <= _MAX_SAFE_INTEGER:
            return value
        return {"type": "bigint", "value": str(value)}
    if isinstance(value, float):
        if math.isnan(value):
            return {"type": "float", "value": "nan"}
        if math.isinf(value):
            return {
                "type": "float",
                "value": "infinity" if value > 0 else "-infinity",
            }
        if value == 0 and math.copysign(1.0, value) < 0:
            return {"type": "float", "value": "negative-zero"}
        return value
    raise TypeError("scalar value must be None, bool, str, int, or float")


def _scalar_from_wire(value: object) -> ScalarValue:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise ValueError("untagged scalar integer exceeds the safe integer range")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("untagged scalar float must be finite")
        return value
    item = _object(value, "scalar.value")
    _exact_fields(item, {"type", "value"}, "scalar.value")
    kind = item["type"]
    encoded = item["value"]
    if kind == "bigint":
        if not isinstance(encoded, str) or _BIGINT.fullmatch(encoded) is None:
            raise ValueError("tagged bigint has an invalid decimal value")
        parsed = int(encoded)
        if abs(parsed) <= _MAX_SAFE_INTEGER:
            raise ValueError("tagged bigint must lie outside the safe integer range")
        return parsed
    if kind == "float":
        if not isinstance(encoded, str) or encoded not in _SPECIAL_FLOATS:
            raise ValueError("tagged float has an invalid value")
        return {
            "nan": math.nan,
            "infinity": math.inf,
            "-infinity": -math.inf,
            "negative-zero": -0.0,
        }[encoded]
    raise ValueError("scalar tag type must be 'bigint' or 'float'")


def _asset_descriptor(asset: AssetRef, provenance: Provenance, label: str) -> None:
    if not isinstance(asset, AssetRef):
        raise TypeError(f"{label} asset must be AssetRef")
    if not isinstance(provenance, Provenance):
        raise TypeError(f"{label} provenance must be Provenance")


def _asset_descriptor_value(
    codec: OutputCodec,
    media_type: str,
    asset: AssetRef,
    provenance: Provenance,
) -> JsonObject:
    return {
        "asset": asset.to_value(),
        "codec": codec,
        "media_type": media_type,
        "provenance": provenance.to_value(),
    }


def _descriptor_asset_facts(descriptor: AssetDescriptor) -> tuple[object, ...]:
    if isinstance(descriptor, BlobAssetDescriptor):
        return (
            descriptor.asset.size,
            descriptor.media_type,
            descriptor.filename,
            canonical_bytes(descriptor.metadata),
        )
    return (descriptor.asset.size, descriptor.media_type)


__all__ = [
    "ARROW_CODEC",
    "ARROW_MEDIA_TYPE",
    "BLOB_ASSET_CODEC",
    "JSON_CODEC",
    "JSON_MEDIA_TYPE",
    "MARIMO_CELL_CODEC",
    "MARIMO_CELL_MEDIA_TYPE",
    "MARIMO_OUTPUT_CODEC",
    "MARIMO_OUTPUT_MEDIA_TYPE",
    "NUMPY_CODEC",
    "NUMPY_MEDIA_TYPE",
    "SCALAR_CODEC",
    "SCALAR_MEDIA_TYPE",
    "ArrowDescriptor",
    "AssetDescriptor",
    "AssetRef",
    "BlobAssetDescriptor",
    "InlineDescriptor",
    "JsonDescriptor",
    "MarimoCellDescriptor",
    "MarimoOutputDescriptor",
    "NumpyDescriptor",
    "OutputCodec",
    "OutputDescriptor",
    "Provenance",
    "ScalarDescriptor",
    "ScalarValue",
    "asset_path",
]
