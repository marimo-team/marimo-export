from __future__ import annotations

import math
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Literal, TypeAlias, cast

from marimo_export._json import (
    JsonObject,
    JsonValue,
    canonical_bytes,
    decode_json_object,
    json_object,
    json_string,
    portable_json_object,
    sha256_bytes,
)
from marimo_export._media_type import MAX_BLOB_METADATA_JSON_BYTES, validate_media_type
from marimo_export._portable import validate_portable_basename
from marimo_export.errors import PublicationError

PUBLICATION_SCHEMA = "marimo-export.publication.v1"
SCALAR_CODEC = "marimo.scalar.v1"
NUMPY_CODEC = "numpy.npy.v1"
ARROW_CODEC = "apache.arrow.file.v1"
BLOB_ASSET_CODEC = "marimo.blob-asset.msgpack.v1"
SCALAR_MEDIA_TYPE = "application/vnd.marimo.scalar.v1+json"
NUMPY_MEDIA_TYPE = "application/x-npy"
ARROW_MEDIA_TYPE = "application/vnd.apache.arrow.file"

OutputCodec: TypeAlias = Literal[
    "marimo.scalar.v1",
    "numpy.npy.v1",
    "apache.arrow.file.v1",
    "marimo.blob-asset.msgpack.v1",
]
ScalarValue: TypeAlias = None | bool | str | int | float
ScalarWireValue: TypeAlias = JsonValue

_CODECS = frozenset({SCALAR_CODEC, NUMPY_CODEC, ARROW_CODEC, BLOB_ASSET_CODEC})
_ASSET_EXTENSIONS: dict[str, str] = {
    NUMPY_CODEC: "npy",
    ARROW_CODEC: "arrow",
    BLOB_ASSET_CODEC: "bin",
}
_MAX_ASSET_SIZE = 2_147_483_647
_MAX_INDEX_BYTES = 16 * 1024 * 1024
_MAX_INDEX_VALUES = 2_000_000
_MAX_NAME_BYTES = 255
_MAX_PROVENANCE_BYTES = 2_048
_MAX_SAFE_INTEGER = 2**53 - 1
_SHA256 = re.compile(r"[0-9a-f]{64}")
_BIGINT = re.compile(r"(?:0|-[1-9][0-9]*|[1-9][0-9]*)")
_SPECIAL_FLOATS = frozenset({"nan", "infinity", "-infinity", "negative-zero"})


@dataclass(frozen=True, slots=True)
class NotebookProvenance:
    filename: str | None
    document_sha256: str

    def __post_init__(self) -> None:
        if self.filename is not None:
            try:
                validate_portable_basename(self.filename, "notebook.filename")
            except (TypeError, ValueError) as error:
                raise ValueError("notebook.filename must be a portable basename or null") from error
        _digest(self.document_sha256, "notebook.document_sha256")

    def to_value(self) -> JsonObject:
        return {
            "filename": self.filename,
            "document_sha256": self.document_sha256,
        }


@dataclass(frozen=True, slots=True)
class ProducerProvenance:
    marimo: str
    marimo_export: str

    def __post_init__(self) -> None:
        _bounded_printable(self.marimo, "producer.marimo", _MAX_NAME_BYTES)
        _bounded_printable(
            self.marimo_export,
            "producer.marimo_export",
            _MAX_NAME_BYTES,
        )

    def to_value(self) -> JsonObject:
        return {"marimo": self.marimo, "marimo_export": self.marimo_export}


@dataclass(frozen=True, slots=True)
class Provenance:
    cache_key: str
    return_reference: str | None
    python_type: str

    def __post_init__(self) -> None:
        _opaque_store_reference(self.cache_key, "provenance.cache_key")
        if self.return_reference is not None:
            _opaque_store_reference(
                self.return_reference,
                "provenance.return_reference",
            )
        _bounded_printable(
            self.python_type,
            "provenance.python_type",
            _MAX_PROVENANCE_BYTES,
        )

    def to_value(self) -> JsonObject:
        return {
            "cache_key": self.cache_key,
            "return_reference": self.return_reference,
            "python_type": self.python_type,
        }


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
        if self.provenance.return_reference is not None:
            raise ValueError("scalar provenance.return_reference must be null")
        _scalar_to_wire(self.value)

    def to_value(self) -> JsonObject:
        return {
            "codec": self.codec,
            "media_type": self.media_type,
            "provenance": self.provenance.to_value(),
            "value": _scalar_to_wire(self.value),
        }


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
    ScalarDescriptor | NumpyDescriptor | ArrowDescriptor | BlobAssetDescriptor
)
AssetDescriptor: TypeAlias = NumpyDescriptor | ArrowDescriptor | BlobAssetDescriptor


@dataclass(frozen=True, slots=True, init=False)
class StateEntry:
    fingerprint: str
    outputs: Mapping[str, OutputDescriptor]
    _inputs_bytes: bytes = field(repr=False)

    def __init__(
        self,
        *,
        inputs: Mapping[str, JsonValue],
        outputs: Mapping[str, OutputDescriptor],
        fingerprint: str | None = None,
    ) -> None:
        input_value = _normalize_input_object(inputs, "state.inputs")
        computed = state_fingerprint(input_value)
        if fingerprint is not None and _digest(fingerprint, "state.fingerprint") != computed:
            raise ValueError("state.fingerprint does not match state.inputs")
        if not isinstance(outputs, Mapping) or not outputs:
            raise ValueError("state.outputs must contain at least one output")
        parsed_outputs: dict[str, OutputDescriptor] = {}
        for name, descriptor in outputs.items():
            public_name = _public_name(name, "state.outputs key")
            if not isinstance(
                descriptor,
                (ScalarDescriptor, NumpyDescriptor, ArrowDescriptor, BlobAssetDescriptor),
            ):
                raise TypeError(f"state.outputs[{public_name!r}] has an invalid descriptor")
            parsed_outputs[public_name] = descriptor
        object.__setattr__(self, "fingerprint", computed)
        object.__setattr__(self, "_inputs_bytes", canonical_bytes(input_value))
        object.__setattr__(self, "outputs", MappingProxyType(parsed_outputs))

    @property
    def inputs(self) -> JsonObject:
        return decode_json_object(self._inputs_bytes, "state.inputs")

    def to_value(self) -> JsonObject:
        return {
            "fingerprint": self.fingerprint,
            "inputs": self.inputs,
            "outputs": {name: descriptor.to_value() for name, descriptor in self.outputs.items()},
        }


@dataclass(frozen=True, slots=True)
class PublicationIndex:
    notebook: NotebookProvenance
    producer: ProducerProvenance
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    states: Mapping[str, StateEntry]

    def __post_init__(self) -> None:
        if not isinstance(self.notebook, NotebookProvenance):
            raise TypeError("publication.notebook must be NotebookProvenance")
        if not isinstance(self.producer, ProducerProvenance):
            raise TypeError("publication.producer must be ProducerProvenance")
        parsed_inputs = _ordered_names(self.inputs, "publication.inputs", identifier=True)
        parsed_outputs = _ordered_names(
            self.outputs,
            "publication.outputs",
            identifier=False,
            nonempty=True,
        )
        if not isinstance(self.states, Mapping) or not self.states:
            raise ValueError("publication.states must contain at least one state")
        parsed_states: dict[str, StateEntry] = {}
        input_set = set(parsed_inputs)
        output_set = set(parsed_outputs)
        fingerprints: dict[bytes, str] = {}
        representation: dict[str, tuple[str, str]] = {}
        for name, state in self.states.items():
            state_name = _public_name(name, "publication.states key")
            if not isinstance(state, StateEntry):
                raise TypeError(f"publication.states[{state_name!r}] must be StateEntry")
            if set(state.inputs) != input_set:
                raise ValueError(
                    f"publication.states[{state_name!r}].inputs must equal publication.inputs"
                )
            if set(state.outputs) != output_set:
                raise ValueError(
                    f"publication.states[{state_name!r}].outputs must equal publication.outputs"
                )
            vector = canonical_bytes(state.inputs)
            other = fingerprints.setdefault(vector, state_name)
            if other != state_name:
                raise ValueError(
                    f"publication states {other!r} and {state_name!r} have equal inputs"
                )
            for output_name, descriptor in state.outputs.items():
                current = (descriptor.codec, descriptor.media_type)
                previous = representation.setdefault(output_name, current)
                if previous != current:
                    raise ValueError(
                        f"output {output_name!r} changes codec or media type across states"
                    )
            parsed_states[state_name] = state
        object.__setattr__(self, "inputs", parsed_inputs)
        object.__setattr__(self, "outputs", parsed_outputs)
        object.__setattr__(self, "states", MappingProxyType(parsed_states))
        self.assets()

    def to_value(self) -> JsonObject:
        return {
            "schema": PUBLICATION_SCHEMA,
            "notebook": self.notebook.to_value(),
            "producer": self.producer.to_value(),
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "states": {name: state.to_value() for name, state in self.states.items()},
        }

    def to_bytes(self) -> bytes:
        data = canonical_bytes(self.to_value())
        if len(data) > _MAX_INDEX_BYTES:
            raise PublicationError(
                f"canonical index exceeds {_MAX_INDEX_BYTES} bytes",
                code="publication_invalid",
            )
        return data

    @classmethod
    def from_value(cls, value: object) -> PublicationIndex:
        try:
            root = json_object(value, "publication")
            _exact_fields(
                root,
                {"schema", "notebook", "producer", "inputs", "outputs", "states"},
                "publication",
            )
            if root["schema"] != PUBLICATION_SCHEMA:
                raise ValueError(f"publication.schema must be {PUBLICATION_SCHEMA!r}")
            notebook = _notebook(root["notebook"])
            producer = _producer(root["producer"])
            inputs = _name_array(root["inputs"], "publication.inputs")
            outputs = _name_array(root["outputs"], "publication.outputs")
            states_value = _object(root["states"], "publication.states")
            states = {
                _public_name(name, "publication.states key"): _state(item, name)
                for name, item in states_value.items()
            }
            return cls(
                notebook=notebook,
                producer=producer,
                inputs=inputs,
                outputs=outputs,
                states=states,
            )
        except PublicationError:
            raise
        except (TypeError, ValueError) as error:
            raise PublicationError(
                f"invalid publication index: {error}",
                code="publication_invalid",
            ) from error

    @classmethod
    def from_bytes(cls, data: bytes) -> PublicationIndex:
        if not isinstance(data, bytes):
            raise TypeError("publication index must be bytes")
        if len(data) > _MAX_INDEX_BYTES:
            raise PublicationError(
                f"publication index exceeds {_MAX_INDEX_BYTES} bytes",
                code="publication_invalid",
            )
        try:
            root = decode_json_object(
                data,
                "publication",
                max_values=_MAX_INDEX_VALUES,
            )
        except (TypeError, ValueError) as error:
            raise PublicationError(
                f"invalid publication index: {error}",
                code="publication_invalid",
            ) from error
        index = cls.from_value(root)
        if index.to_bytes() != data:
            raise PublicationError(
                "publication index is not canonical JSON",
                code="publication_noncanonical",
            )
        return index

    def assets(self) -> tuple[tuple[OutputCodec, AssetRef], ...]:
        assets: dict[tuple[str, str], tuple[AssetRef, tuple[object, ...]]] = {}
        total = 0
        for _, _, descriptor in self.descriptor_entries():
            if isinstance(descriptor, ScalarDescriptor):
                continue
            identity = (descriptor.codec, descriptor.asset.sha256)
            facts = _descriptor_asset_facts(descriptor)
            previous = assets.get(identity)
            if previous is None:
                assets[identity] = (descriptor.asset, facts)
                total += descriptor.asset.size
                if total > _MAX_SAFE_INTEGER:
                    raise ValueError("aggregate unique asset size exceeds the safe integer range")
            elif previous != (descriptor.asset, facts):
                raise ValueError(f"asset identity {identity!r} has conflicting descriptor facts")
        order = {NUMPY_CODEC: 0, ARROW_CODEC: 1, BLOB_ASSET_CODEC: 2}
        return tuple(
            (cast(OutputCodec, codec), value[0])
            for (codec, _), value in sorted(
                assets.items(),
                key=lambda item: (order[item[0][0]], item[0][1]),
            )
        )

    def descriptor_entries(self) -> Iterator[tuple[str, str, OutputDescriptor]]:
        for state_name, state in self.states.items():
            for output_name, descriptor in state.outputs.items():
                yield state_name, output_name, descriptor


@dataclass(frozen=True, slots=True)
class CacheSummary:
    hits: int
    misses: int

    def __post_init__(self) -> None:
        for name, value in (("hits", self.hits), ("misses", self.misses)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"cache.{name} must be a non-negative integer")


@dataclass(frozen=True, slots=True, init=False)
class PublicationWarning:
    code: Literal["retired_destination_cleanup_failed"]
    message: str
    _details_bytes: bytes = field(repr=False)

    def __init__(
        self,
        *,
        code: Literal["retired_destination_cleanup_failed"],
        message: str,
        details: Mapping[str, JsonValue],
    ) -> None:
        if code != "retired_destination_cleanup_failed":
            raise ValueError("publication warning code is invalid")
        _bounded_printable(message, "publication warning message", _MAX_PROVENANCE_BYTES)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "message", message)
        object.__setattr__(
            self,
            "_details_bytes",
            canonical_bytes(json_object(details, "publication warning details")),
        )

    @property
    def details(self) -> JsonObject:
        return decode_json_object(self._details_bytes, "publication warning details")

    def to_dict(self) -> JsonObject:
        return {"code": self.code, "message": self.message, "details": self.details}


@dataclass(frozen=True, slots=True)
class PublicationResult:
    path: Path
    mode: Literal["build", "capture"]
    session_id: str | None
    notebook_filename: str | None
    document_sha256: str
    producer: ProducerProvenance
    states: tuple[str, ...]
    outputs: tuple[str, ...]
    assets: int
    asset_bytes: int
    index_bytes: int
    cache: CacheSummary
    warnings: tuple[PublicationWarning, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.path, Path) or not self.path.is_absolute():
            raise ValueError("publication result path must be absolute")
        if self.mode not in {"build", "capture"}:
            raise ValueError("publication result mode must be build or capture")
        if self.session_id is not None:
            _bounded_printable(self.session_id, "session_id", _MAX_PROVENANCE_BYTES)
        if self.notebook_filename is not None:
            validate_portable_basename(self.notebook_filename, "notebook_filename")
        _digest(self.document_sha256, "document_sha256")
        if not isinstance(self.producer, ProducerProvenance):
            raise TypeError("producer must be ProducerProvenance")
        _ordered_names(self.states, "states", identifier=False)
        _ordered_names(self.outputs, "outputs", identifier=False, nonempty=True)
        for name, value in (
            ("assets", self.assets),
            ("asset_bytes", self.asset_bytes),
            ("index_bytes", self.index_bytes),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not isinstance(self.cache, CacheSummary):
            raise TypeError("cache must be CacheSummary")
        if any(not isinstance(warning, PublicationWarning) for warning in self.warnings):
            raise TypeError("warnings must contain PublicationWarning values")

    def to_dict(self) -> JsonObject:
        return {
            "path": str(self.path),
            "mode": self.mode,
            "session_id": self.session_id,
            "notebook_filename": self.notebook_filename,
            "document_sha256": self.document_sha256,
            "producer": self.producer.to_value(),
            "states": list(self.states),
            "outputs": list(self.outputs),
            "assets": self.assets,
            "asset_bytes": self.asset_bytes,
            "index_bytes": self.index_bytes,
            "cache": {"hits": self.cache.hits, "misses": self.cache.misses},
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


def state_fingerprint(inputs: Mapping[str, JsonValue]) -> str:
    return sha256_bytes(canonical_bytes(_normalize_input_object(inputs, "state.inputs")))


def asset_path(codec: OutputCodec, digest: str) -> str:
    if codec not in _ASSET_EXTENSIONS:
        raise ValueError(f"codec {codec!r} has no asset path")
    validated = _digest(digest, "asset digest")
    return f"assets/{validated}.{_ASSET_EXTENSIONS[codec]}"


def _notebook(value: object) -> NotebookProvenance:
    item = _object(value, "publication.notebook")
    _exact_fields(item, {"filename", "document_sha256"}, "publication.notebook")
    filename = item["filename"]
    if filename is not None and not isinstance(filename, str):
        raise TypeError("publication.notebook.filename must be a string or null")
    return NotebookProvenance(
        filename=filename,
        document_sha256=cast(str, item["document_sha256"]),
    )


def _producer(value: object) -> ProducerProvenance:
    item = _object(value, "publication.producer")
    _exact_fields(item, {"marimo", "marimo_export"}, "publication.producer")
    return ProducerProvenance(
        marimo=cast(str, item["marimo"]),
        marimo_export=cast(str, item["marimo_export"]),
    )


def _state(value: object, state_name: str) -> StateEntry:
    path = f"publication.states[{state_name!r}]"
    item = _object(value, path)
    _exact_fields(item, {"fingerprint", "inputs", "outputs"}, path)
    output_values = _object(item["outputs"], f"{path}.outputs")
    return StateEntry(
        fingerprint=cast(str, item["fingerprint"]),
        inputs=_object(item["inputs"], f"{path}.inputs"),
        outputs={
            _public_name(name, f"{path}.outputs key"): _descriptor(descriptor, name)
            for name, descriptor in output_values.items()
        },
    )


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
    _exact_fields(
        item,
        {"cache_key", "return_reference", "python_type"},
        f"{path}.provenance",
    )
    reference = item["return_reference"]
    if reference is not None and not isinstance(reference, str):
        raise TypeError(f"{path}.provenance.return_reference must be a string or null")
    return Provenance(
        cache_key=cast(str, item["cache_key"]),
        return_reference=reference,
        python_type=cast(str, item["python_type"]),
    )


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
    if provenance.return_reference is None:
        raise ValueError(f"{label} provenance.return_reference must be present")


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


def _object(value: object, path: str) -> JsonObject:
    return json_object(value, path)


def _exact_fields(value: Mapping[str, object], expected: set[str], path: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        raise ValueError(f"{path} is missing fields: {', '.join(missing)}")
    if extra:
        raise ValueError(f"{path} does not accept fields: {', '.join(extra)}")


def _name_array(value: object, path: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{path} must be an array")
    return tuple(cast(str, item) for item in value)


def _ordered_names(
    value: Sequence[str],
    path: str,
    *,
    identifier: bool,
    nonempty: bool = False,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{path} must be a sequence")
    result = tuple(
        _identifier(name, f"{path}[{index}]")
        if identifier
        else _public_name(name, f"{path}[{index}]")
        for index, name in enumerate(value)
    )
    if nonempty and not result:
        raise ValueError(f"{path} must contain at least one name")
    if len(result) != len(set(result)):
        raise ValueError(f"{path} must contain unique names")
    return result


def _identifier(value: object, path: str) -> str:
    name = json_string(value, path)
    if (
        not name.isidentifier()
        or __import__("keyword").iskeyword(name)
        or len(name.encode("utf-8")) > _MAX_NAME_BYTES
    ):
        raise ValueError(f"{path} must be a bounded non-keyword Python identifier")
    return name


def _public_name(value: object, path: str) -> str:
    return _bounded_printable(value, path, _MAX_NAME_BYTES)


def _bounded_printable(value: object, path: str, maximum_bytes: int) -> str:
    text = json_string(value, path)
    if (
        not text
        or text != text.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in text)
        or len(text.encode("utf-8")) > maximum_bytes
    ):
        raise ValueError(
            f"{path} must be a non-empty printable string of at most {maximum_bytes} UTF-8 bytes"
        )
    return text


def _opaque_store_reference(value: object, path: str) -> str:
    text = _bounded_printable(value, path, _MAX_PROVENANCE_BYTES)
    if PurePosixPath(text).is_absolute() or PureWindowsPath(text).is_absolute():
        raise ValueError(f"{path} must be a store-relative opaque identifier")
    return text


def _digest(value: object, path: str) -> str:
    text = json_string(value, path)
    if _SHA256.fullmatch(text) is None:
        raise ValueError(f"{path} must be a lowercase SHA-256 digest")
    return text


def _normalize_input_object(value: Mapping[str, JsonValue], path: str) -> JsonObject:
    parsed = json_object(value, path)
    _validate_portable_input_numbers(parsed, path)
    return cast(JsonObject, _normalize_zero(parsed))


def _normalize_zero(value: JsonValue) -> JsonValue:
    if isinstance(value, float) and value == 0:
        return 0
    if isinstance(value, list):
        return [_normalize_zero(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_zero(item) for key, item in value.items()}
    return value


def _validate_portable_input_numbers(value: JsonValue, path: str) -> None:
    pending: list[tuple[JsonValue, str]] = [(value, path)]
    while pending:
        item, item_path = pending.pop()
        if isinstance(item, bool) or item is None or isinstance(item, str):
            continue
        if isinstance(item, int):
            if abs(item) > _MAX_SAFE_INTEGER:
                raise ValueError(f"{item_path} integer exceeds the safe integer range")
            continue
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError(f"{item_path} must be finite")
            if item.is_integer() and abs(item) > _MAX_SAFE_INTEGER:
                raise ValueError(f"{item_path} integer exceeds the safe integer range")
            continue
        if isinstance(item, list):
            pending.extend((child, f"{item_path}[{index}]") for index, child in enumerate(item))
            continue
        pending.extend(
            (child, f"{item_path}.{key}") for key, child in cast(dict[str, JsonValue], item).items()
        )


__all__ = [
    "ARROW_CODEC",
    "ARROW_MEDIA_TYPE",
    "BLOB_ASSET_CODEC",
    "NUMPY_CODEC",
    "NUMPY_MEDIA_TYPE",
    "PUBLICATION_SCHEMA",
    "SCALAR_CODEC",
    "SCALAR_MEDIA_TYPE",
    "ArrowDescriptor",
    "AssetDescriptor",
    "AssetRef",
    "BlobAssetDescriptor",
    "CacheSummary",
    "NotebookProvenance",
    "NumpyDescriptor",
    "OutputCodec",
    "OutputDescriptor",
    "ProducerProvenance",
    "Provenance",
    "PublicationIndex",
    "PublicationResult",
    "PublicationWarning",
    "ScalarDescriptor",
    "ScalarValue",
    "StateEntry",
    "asset_path",
    "state_fingerprint",
]
