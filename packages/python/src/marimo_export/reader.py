from __future__ import annotations

import ast
import errno
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import NoReturn, cast

import marimo_export._snapshot as _snapshot
from marimo_export._blob_asset import BlobAssetEnvelope, decode_blob_asset
from marimo_export._json import (
    JsonObject,
    JsonValue,
    canonical_bytes,
    sha256_bytes,
)
from marimo_export._limits import MAX_EXPORT_ASSET_BYTES, MAX_EXPORT_CLOSURE_BYTES
from marimo_export._secure_io import (
    SecureReadError,
    SecureReadUnavailableError,
    read_export_asset,
    read_export_index,
)
from marimo_export.descriptors import (
    ArrowDescriptor,
    BlobAssetDescriptor,
    JsonDescriptor,
    MarimoCellDescriptor,
    MarimoOutputDescriptor,
    NumpyDescriptor,
    OutputDescriptor,
    ScalarDescriptor,
    ScalarValue,
    asset_path,
)
from marimo_export.errors import (
    ExportUnavailableError,
    IntegrityError,
    NotebookExportError,
    StateUnavailableError,
)
from marimo_export.index import (
    ControlBinding,
    ExportIndex,
    NotebookProvenance,
    ProducerProvenance,
    StateEntry,
)
from marimo_export.outputs import BlobAsset
from marimo_export.spec import FrozenJsonObject, FrozenJsonValue, StrPath
from marimo_export.wire import state_fingerprint

_MAX_INDEX_BYTES = 16 * 1024 * 1024
_NPY_MAX_HEADER_BYTES = 1024 * 1024


class _Immutable:
    __slots__ = ()

    def __setattr__(self, name: str, value: object) -> None:
        try:
            object.__getattribute__(self, name)
        except AttributeError:
            object.__setattr__(self, name, value)
            return
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __delattr__(self, name: str) -> None:
        del name
        raise AttributeError(f"{type(self).__name__} is immutable")


@dataclass(frozen=True, slots=True)
class VerificationResult:
    states: int
    outputs: int
    assets: int
    bytes_verified: int

    def __post_init__(self) -> None:
        for name in ("states", "outputs", "assets", "bytes_verified"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"verification result {name} must be a nonnegative integer")

    def to_dict(self) -> JsonObject:
        return {
            "states": self.states,
            "outputs": self.outputs,
            "assets": self.assets,
            "bytes_verified": self.bytes_verified,
        }


class NotebookExport(_Immutable):
    """An immutable local export opened from canonical `index.json`."""

    __slots__ = ("_aliases", "_identity", "_index", "_path", "_states", "_vectors")

    def __init__(self, path: Path, index: ExportIndex, identity: str) -> None:
        self._path = path
        self._index = index
        self._identity = identity
        aliases_by_fingerprint: dict[str, list[str]] = {
            fingerprint: [] for fingerprint in index.states
        }
        for alias, fingerprint in sorted(index.aliases.items()):
            aliases_by_fingerprint[fingerprint].append(alias)
        self._states = {
            fingerprint: ExportState(
                self,
                fingerprint,
                tuple(aliases_by_fingerprint[fingerprint]),
                entry,
            )
            for fingerprint, entry in sorted(index.states.items())
        }
        self._aliases = {
            alias: self._states[fingerprint] for alias, fingerprint in sorted(index.aliases.items())
        }
        self._vectors = {
            canonical_bytes(entry.inputs): self._states[fingerprint]
            for fingerprint, entry in index.states.items()
        }

    @property
    def path(self) -> Path:
        return self._path

    @property
    def identity(self) -> str:
        """SHA-256 of the canonical `index.json` bytes."""

        return self._identity

    @property
    def spec_sha256(self) -> str:
        """SHA-256 of the canonical export specification used by the producer."""

        return self._index.spec_sha256

    @property
    def default_state(self) -> ExportState:
        """Return the state selected as the export's initial state."""

        return self._states[self._index.default_state]

    @property
    def input_names(self) -> tuple[str, ...]:
        return self._index.inputs

    @property
    def control_bindings(self) -> Mapping[str, ControlBinding]:
        return self._index.control_bindings

    @property
    def output_names(self) -> tuple[str, ...]:
        return self._index.outputs

    @property
    def notebook(self) -> NotebookProvenance:
        return self._index.notebook

    @property
    def producer(self) -> ProducerProvenance:
        return self._index.producer

    def states(self) -> tuple[ExportState, ...]:
        return tuple(self._states.values())

    def state(self, alias: str) -> ExportState:
        if not isinstance(alias, str):
            raise TypeError("state alias must be a string")
        try:
            return self._aliases[alias]
        except KeyError as error:
            raise NotebookExportError(
                f"export state alias {alias!r} was not found",
                code="state_not_found",
                details={"alias": alias, "available": list(self._aliases)[:16]},
            ) from error

    def state_by_fingerprint(self, fingerprint: str) -> ExportState:
        if (
            not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
        ):
            raise TypeError("state fingerprint must be a lowercase SHA-256 digest")
        try:
            return self._states[fingerprint]
        except KeyError as error:
            raise NotebookExportError(
                f"export state fingerprint {fingerprint!r} was not found",
                code="state_not_found",
                details={
                    "fingerprint": fingerprint,
                    "available": list(self._states)[:16],
                },
            ) from error

    def resolve(self, inputs: Mapping[str, JsonValue]) -> ExportState:
        vector = _complete_inputs(inputs, self.input_names)
        try:
            return self._vectors[canonical_bytes(vector)]
        except KeyError as error:
            raise StateUnavailableError(
                "export has no state for the requested input vector",
                details={"fingerprint": state_fingerprint(vector)},
            ) from error

    def verify(self) -> VerificationResult:
        declared = {asset_path(codec, asset.sha256) for codec, asset in self._index.assets()}
        _verify_asset_directory(self.path, declared)
        verified: set[tuple[str, str]] = set()
        total = 0
        for _, _, descriptor in self._index.descriptor_entries():
            if isinstance(descriptor, (ScalarDescriptor, JsonDescriptor)):
                continue
            identity = (descriptor.codec, descriptor.asset.sha256)
            if identity in verified:
                continue
            data = _read_asset(self.path, descriptor)
            _validate_asset(descriptor, data)
            verified.add(identity)
            total += len(data)
        return VerificationResult(
            states=len(self._index.states),
            outputs=len(self._index.states) * len(self._index.outputs),
            assets=len(verified),
            bytes_verified=total,
        )


class ExportState(_Immutable):
    __slots__ = (
        "_entry",
        "_inputs",
        "_notebook_export",
        "_outputs",
        "aliases",
        "fingerprint",
    )

    def __init__(
        self,
        notebook_export: NotebookExport,
        fingerprint: str,
        aliases: tuple[str, ...],
        entry: StateEntry,
    ) -> None:
        self._notebook_export = notebook_export
        self.fingerprint = fingerprint
        self.aliases = aliases
        self._entry = entry
        self._inputs = cast(FrozenJsonObject, _freeze(entry.inputs))
        self._outputs = {
            output_name: ExportOutput(self, output_name, entry.outputs[output_name])
            for output_name in notebook_export.output_names
        }

    @property
    def notebook_export(self) -> NotebookExport:
        return self._notebook_export

    @property
    def inputs(self) -> FrozenJsonObject:
        return self._inputs

    def outputs(self) -> tuple[ExportOutput, ...]:
        return tuple(self._outputs.values())

    def output(self, name: str) -> ExportOutput:
        if not isinstance(name, str):
            raise TypeError("output name must be a string")
        try:
            return self._outputs[name]
        except KeyError as error:
            raise NotebookExportError(
                f"export output {name!r} was not found",
                code="output_not_found",
                details={"name": name, "available": list(self._outputs)[:16]},
            ) from error

    def resolve(self, patch: Mapping[str, JsonValue]) -> ExportState:
        if not isinstance(patch, Mapping):
            raise NotebookExportError(
                "state patch must be an object",
                code="state_input_invalid",
            )
        if not patch:
            return self
        unknown = sorted(set(patch) - set(self.notebook_export.input_names))
        if unknown:
            raise NotebookExportError(
                f"state patch names unknown inputs: {', '.join(unknown)}",
                code="state_input_invalid",
                details={"unknown": unknown},
            )
        merged = _thaw(self.inputs)
        assert isinstance(merged, dict)
        for name, value in patch.items():
            merged[name] = value
        return self.notebook_export.resolve(cast(Mapping[str, JsonValue], merged))


class ExportOutput(_Immutable):
    __slots__ = ("_descriptor", "_state", "name")

    def __init__(
        self,
        state: ExportState,
        name: str,
        descriptor: OutputDescriptor,
    ) -> None:
        self._state = state
        self.name = name
        self._descriptor = descriptor

    @property
    def state(self) -> ExportState:
        return self._state

    @property
    def codec(self) -> str:
        return self._descriptor.codec

    @property
    def media_type(self) -> str:
        return self._descriptor.media_type

    @property
    def descriptor(self) -> OutputDescriptor:
        return self._descriptor

    def scalar(self) -> ScalarValue:
        if not isinstance(self._descriptor, ScalarDescriptor):
            raise NotebookExportError(
                f"output {self.name!r} does not use the scalar codec",
                code="codec_invalid",
            )
        return self._descriptor.value

    def json(self) -> FrozenJsonValue:
        """Return one immutable JSON projection value."""

        if not isinstance(self._descriptor, JsonDescriptor):
            raise NotebookExportError(
                f"output {self.name!r} does not use the JSON codec",
                code="codec_invalid",
            )
        return cast(FrozenJsonValue, self._descriptor.value)

    def asset_bytes(self) -> bytes:
        if isinstance(self._descriptor, (ScalarDescriptor, JsonDescriptor)):
            raise NotebookExportError(
                f"output {self.name!r} has no asset bytes",
                code="codec_invalid",
            )
        data = _read_asset(self.state.notebook_export.path, self._descriptor)
        _validate_asset(self._descriptor, data)
        return data

    def blob_asset(self) -> BlobAsset:
        if not isinstance(self._descriptor, BlobAssetDescriptor):
            raise NotebookExportError(
                f"output {self.name!r} does not use the BlobAsset codec",
                code="codec_invalid",
            )
        envelope = _validated_blob(self._descriptor, self.asset_bytes())
        return BlobAsset(
            data=envelope.data,
            media_type=envelope.media_type,
            filename=envelope.filename,
            metadata=envelope.metadata,
        )


def open_export(path: StrPath) -> NotebookExport:
    """Open and validate a local export index without reading assets."""

    root = _export_root(path)
    try:
        data = read_export_index(root, max_bytes=_MAX_INDEX_BYTES)
    except SecureReadUnavailableError as error:
        raise ExportUnavailableError(
            f"export index storage is unavailable: {error}",
            details={"path": str(root)},
        ) from error
    except (PermissionError, TimeoutError, MemoryError) as error:
        raise ExportUnavailableError(
            "export index storage is unavailable",
            details={"path": str(root)},
        ) from error
    except SecureReadError as error:
        raise NotebookExportError(
            f"could not read export index: {error}",
            code="export_invalid",
        ) from error
    index = ExportIndex.from_bytes(data)
    closure = len(data) + sum(asset.size for _, asset in index.assets())
    if closure > MAX_EXPORT_CLOSURE_BYTES:
        raise NotebookExportError(
            f"export closure exceeds {MAX_EXPORT_CLOSURE_BYTES} bytes",
            code="export_invalid",
        )
    if any(asset.size > MAX_EXPORT_ASSET_BYTES for _, asset in index.assets()):
        raise NotebookExportError(
            f"export asset exceeds {MAX_EXPORT_ASSET_BYTES} bytes",
            code="export_invalid",
        )
    return NotebookExport(root, index, sha256_bytes(data))


def _export_root(path: StrPath) -> Path:
    if not isinstance(path, (str, os.PathLike)):
        raise TypeError("export path must be a string or path-like object")
    requested = Path(path).expanduser().absolute()
    try:
        inspected = requested.lstat()
    except (OSError, MemoryError) as error:
        _raise_path_inspection_error(
            error,
            path=requested,
            invalid="export directory is missing or structurally invalid",
            unavailable="export directory storage is unavailable",
            code="export_invalid",
        )
    if stat.S_ISLNK(inspected.st_mode) or not stat.S_ISDIR(inspected.st_mode):
        raise NotebookExportError(
            "export root must be a real directory",
            code="export_invalid",
        )
    try:
        return requested.resolve(strict=True)
    except RuntimeError as error:
        raise NotebookExportError(
            f"export directory is structurally invalid: {requested}",
            code="export_invalid",
        ) from error
    except (OSError, MemoryError) as error:
        _raise_path_inspection_error(
            error,
            path=requested,
            invalid="export directory is missing or structurally invalid",
            unavailable="export directory storage is unavailable",
            code="export_invalid",
        )


def _complete_inputs(
    inputs: Mapping[str, JsonValue],
    names: tuple[str, ...],
) -> JsonObject:
    if not isinstance(inputs, Mapping):
        raise NotebookExportError(
            "state inputs must be an object",
            code="state_input_invalid",
        )
    expected = set(names)
    actual = set(inputs)
    if actual != expected:
        raise NotebookExportError(
            "state input keys must exactly match export.input_names",
            code="state_input_invalid",
            details={
                "missing": sorted(expected - actual),
                "extra": sorted(actual - expected),
            },
        )
    try:
        return {name: cast(JsonValue, _thaw(_freeze(inputs[name]))) for name in names}
    except (TypeError, ValueError) as error:
        raise NotebookExportError(
            f"state inputs are invalid: {error}",
            code="state_input_invalid",
        ) from error


def _read_asset(path: Path, descriptor: OutputDescriptor) -> bytes:
    if isinstance(descriptor, (ScalarDescriptor, JsonDescriptor)):
        raise TypeError("inline descriptors have no assets")
    relative = asset_path(descriptor.codec, descriptor.asset.sha256)
    try:
        data = read_export_asset(
            path,
            relative,
            expected_size=descriptor.asset.size,
            max_bytes=MAX_EXPORT_ASSET_BYTES,
        )
    except SecureReadUnavailableError as error:
        raise ExportUnavailableError(
            f"export asset storage is unavailable: {relative}",
            details={"path": relative},
        ) from error
    except (PermissionError, TimeoutError, MemoryError) as error:
        raise ExportUnavailableError(
            f"export asset storage is unavailable: {relative}",
            details={"path": relative},
        ) from error
    except SecureReadError as error:
        raise IntegrityError(
            f"could not read export asset {relative}: {error}",
            details={"path": relative},
        ) from error
    from marimo_export._json import sha256_bytes

    digest = sha256_bytes(data)
    if digest != descriptor.asset.sha256:
        raise IntegrityError(
            f"export asset {relative} failed SHA-256 verification",
            details={"path": relative},
        )
    return data


def _validate_asset(descriptor: OutputDescriptor, data: bytes) -> None:
    if isinstance(descriptor, NumpyDescriptor):
        _validate_npy(data)
    elif isinstance(descriptor, ArrowDescriptor):
        _validate_arrow(data)
    elif isinstance(descriptor, BlobAssetDescriptor):
        _validated_blob(descriptor, data)
    elif isinstance(descriptor, MarimoOutputDescriptor):
        _validate_snapshot(data, "marimo.output.v1")
    elif isinstance(descriptor, MarimoCellDescriptor):
        _validate_snapshot(data, "marimo.cell.v1")


def _validate_snapshot(data: bytes, schema: _snapshot.SnapshotSchema) -> None:
    try:
        _snapshot.validate_snapshot(data, schema)
    except (TypeError, ValueError) as error:
        raise IntegrityError(
            f"{schema} snapshot is invalid: {error}",
            code="asset_invalid",
        ) from error


def _validated_blob(
    descriptor: BlobAssetDescriptor,
    data: bytes,
) -> BlobAssetEnvelope:
    try:
        envelope = decode_blob_asset(data, maximum_bytes=MAX_EXPORT_ASSET_BYTES)
    except (TypeError, ValueError) as error:
        raise IntegrityError(
            "BlobAsset envelope is invalid",
            code="asset_invalid",
        ) from error
    if envelope.media_type != descriptor.media_type:
        raise IntegrityError(
            "BlobAsset media type disagrees with its descriptor",
            code="asset_invalid",
        )
    if envelope.filename != descriptor.filename:
        raise IntegrityError(
            "BlobAsset filename disagrees with its descriptor",
            code="asset_invalid",
        )
    if canonical_bytes(envelope.metadata) != canonical_bytes(descriptor.metadata):
        raise IntegrityError(
            "BlobAsset metadata disagrees with its descriptor",
            code="asset_invalid",
        )
    return envelope


def _validate_npy(data: bytes) -> None:
    if len(data) < 10 or data[:6] != b"\x93NUMPY":
        raise IntegrityError("NumPy asset has invalid NPY magic", code="asset_invalid")
    major, minor = data[6], data[7]
    if (major, minor) not in {(1, 0), (2, 0), (3, 0)}:
        raise IntegrityError("NumPy asset uses an unsupported NPY version", code="asset_invalid")
    length_bytes = 2 if major == 1 else 4
    header_start = 8 + length_bytes
    if len(data) < header_start:
        raise IntegrityError("NumPy asset has a truncated NPY header", code="asset_invalid")
    header_length = int.from_bytes(data[8:header_start], "little")
    if not 0 < header_length <= _NPY_MAX_HEADER_BYTES:
        raise IntegrityError("NumPy asset has an invalid NPY header length", code="asset_invalid")
    header_end = header_start + header_length
    if header_end > len(data):
        raise IntegrityError("NumPy asset has a truncated NPY header", code="asset_invalid")
    try:
        header = ast.literal_eval(data[header_start:header_end].decode("latin1").strip())
    except (SyntaxError, ValueError, UnicodeError) as error:
        raise IntegrityError(
            "NumPy asset has an invalid NPY header", code="asset_invalid"
        ) from error
    if not isinstance(header, dict) or set(header) != {"descr", "fortran_order", "shape"}:
        raise IntegrityError("NumPy asset has an invalid NPY header", code="asset_invalid")
    descr = header["descr"]
    order = header["fortran_order"]
    shape = header["shape"]
    if not isinstance(descr, str) or len(descr) < 2:
        raise IntegrityError("NumPy asset has an invalid dtype", code="asset_invalid")
    if descr[1:2] not in {"?", "b", "i", "u", "f", "c"}:
        raise IntegrityError("NumPy asset dtype is outside the portable set", code="asset_invalid")
    try:
        item_size = int(descr[2:] or "1")
    except ValueError as error:
        raise IntegrityError(
            "NumPy asset has an invalid dtype size", code="asset_invalid"
        ) from error
    if item_size <= 0 or not isinstance(order, bool) or not isinstance(shape, tuple):
        raise IntegrityError("NumPy asset has an invalid NPY header", code="asset_invalid")
    items = 1
    for dimension in shape:
        if (
            not isinstance(dimension, int)
            or isinstance(dimension, bool)
            or dimension < 0
            or dimension > 2**53 - 1
        ):
            raise IntegrityError("NumPy asset has an invalid shape", code="asset_invalid")
        items *= dimension
        if items > 2**53 - 1:
            raise IntegrityError("NumPy asset shape is too large", code="asset_invalid")
    if header_end + items * item_size != len(data):
        raise IntegrityError(
            "NumPy asset payload length does not match its shape", code="asset_invalid"
        )


def _validate_arrow(data: bytes) -> None:
    if len(data) < 16 or data[:6] != b"ARROW1" or data[-6:] != b"ARROW1":
        raise IntegrityError("Arrow asset has invalid file framing", code="asset_invalid")
    footer_length = int.from_bytes(data[-10:-6], "little")
    if footer_length <= 0 or footer_length > len(data) - 16:
        raise IntegrityError("Arrow asset has an invalid footer length", code="asset_invalid")


def _verify_asset_directory(root: Path, declared: set[str]) -> None:
    directory = root / "assets"
    try:
        inspected = directory.lstat()
    except FileNotFoundError:
        if declared:
            raise NotebookExportError(
                "export assets directory is missing",
                code="asset_invalid",
            ) from None
        return
    except (OSError, MemoryError) as error:
        _raise_path_inspection_error(
            error,
            path=directory,
            invalid="export assets directory is missing or structurally invalid",
            unavailable="export assets directory storage is unavailable",
            code="asset_invalid",
        )
    if stat.S_ISLNK(inspected.st_mode) or not stat.S_ISDIR(inspected.st_mode):
        raise NotebookExportError(
            "export assets path must be a real directory",
            code="asset_invalid",
        )
    expected = {Path(path).name for path in declared}
    try:
        entries = tuple(directory.iterdir())
    except (OSError, MemoryError) as error:
        _raise_path_inspection_error(
            error,
            path=directory,
            invalid="export assets directory could not be enumerated",
            unavailable="export assets directory storage is unavailable",
            code="asset_invalid",
        )
    actual: set[str] = set()
    for entry in entries:
        try:
            entry_stat = entry.lstat()
        except (OSError, MemoryError) as error:
            _raise_path_inspection_error(
                error,
                path=entry,
                invalid="export asset is missing or structurally invalid",
                unavailable="export asset storage is unavailable",
                code="asset_invalid",
            )
        if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISREG(entry_stat.st_mode):
            raise NotebookExportError(
                f"export contains an undeclared asset entry: {entry.name}",
                code="asset_undeclared",
            )
        actual.add(entry.name)
    undeclared = sorted(actual - expected)
    if undeclared:
        raise NotebookExportError(
            f"export contains undeclared assets: {', '.join(undeclared[:16])}",
            code="asset_undeclared",
            details={"assets": undeclared[:16]},
        )


def _raise_path_inspection_error(
    error: OSError | MemoryError,
    *,
    path: Path,
    invalid: str,
    unavailable: str,
    code: str,
) -> NoReturn:
    if isinstance(error, OSError) and error.errno in {
        errno.ENOENT,
        errno.ENOTDIR,
        errno.EISDIR,
        errno.ELOOP,
    }:
        raise NotebookExportError(invalid, code=code) from error
    raise ExportUnavailableError(
        unavailable,
        details={"path": str(path)},
    ) from error


def _freeze(value: object) -> FrozenJsonValue:
    if value is None or isinstance(value, (str, bool, int, float)):
        if isinstance(value, float) and not __import__("math").isfinite(value):
            raise ValueError("JSON values must be finite")
        return value
    if isinstance(value, Mapping):
        result: dict[str, FrozenJsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            result[key] = _freeze(item)
        return MappingProxyType(result)
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    raise TypeError(f"value must be portable JSON, got {type(value).__name__}")


def _thaw(value: FrozenJsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[str, FrozenJsonValue], value)
        return {key: _thaw(item) for key, item in mapping.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


__all__ = [
    "ExportOutput",
    "ExportState",
    "NotebookExport",
    "NotebookProvenance",
    "ProducerProvenance",
    "VerificationResult",
    "open_export",
]
