from __future__ import annotations

import builtins
from bisect import insort
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from marimo_export._blob_asset import (
    _BlobAssetFieldLimitError,
    decode_blob_asset_wire,
)
from marimo_export._json import (
    JsonObject,
    JsonValue,
    decode_json,
    decode_json_object,
    json_equal,
    json_object,
    sha256_bytes,
)
from marimo_export._portable import validate_portable_basename
from marimo_export._secure_io import (
    SecureFileSizeError,
    SecureReadError,
    SecureReadLimitError,
    read_cache_asset,
    read_publication_index,
)
from marimo_export.errors import IntegrityError, PublicationError
from marimo_export.publication import (
    ASSET_CODEC,
    PUBLICATION_SCHEMA,
    AssetRef,
    FormatEntry,
    OutputEntry,
    PublicationIndex,
    VariantEntry,
)
from marimo_export.publication import (
    NotebookProvenance as _NotebookProvenance,
)
from marimo_export.publication import (
    ProducerProvenance as _ProducerProvenance,
)

_DEFAULT_MAX_INDEX_BYTES = 16 * 1024 * 1024
_DEFAULT_MAX_ASSET_BYTES = 64 * 1024 * 1024
_DEFAULT_MAX_PUBLICATION_BYTES = 512 * 1024 * 1024
_NOT_FOUND_AVAILABLE_LIMIT = 16
_NOT_FOUND_AVAILABLE_UTF8_LIMIT = 2_048
_NOT_FOUND_NAME_UTF8_LIMIT = 2_048
_NOT_FOUND_MESSAGE_LIMIT = 4_096
_TRUNCATION_MARKER = "..."


class _PublicationNotFoundError(PublicationError):
    code = "not_found"


@dataclass(frozen=True, slots=True)
class _BlobAsset:
    data: memoryview
    media_type: str
    filename: str | None
    format_id: str
    metadata: JsonObject


@runtime_checkable
class NotebookProvenance(Protocol):
    @property
    def filename(self) -> str: ...

    @property
    def document_sha256(self) -> str: ...


@runtime_checkable
class ProducerProvenance(Protocol):
    @property
    def marimo(self) -> str: ...

    @property
    def marimo_export(self) -> str: ...


@runtime_checkable
class Publication(Protocol):
    @property
    def notebook(self) -> NotebookProvenance: ...

    @property
    def producer(self) -> ProducerProvenance: ...

    @property
    def variant_names(self) -> tuple[str, ...]: ...

    def variant(self, name: str) -> PublishedVariant: ...

    def describe(self) -> JsonObject: ...

    def verify(self) -> int: ...


@runtime_checkable
class PublishedVariant(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def controls(self) -> JsonObject: ...

    @property
    def outputs(self) -> tuple[str, ...]: ...

    def output(self, name: str) -> PublishedOutput: ...


@runtime_checkable
class PublishedOutput(Protocol):
    @property
    def variant(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def formats(self) -> tuple[str, ...]: ...

    def format(self, name: str) -> PublishedFormat: ...


@runtime_checkable
class PublishedFormat(Protocol):
    @property
    def variant(self) -> str: ...

    @property
    def output(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def format_id(self) -> str: ...

    @property
    def media_type(self) -> str: ...

    @property
    def metadata(self) -> JsonObject: ...

    @property
    def filename(self) -> str | None: ...

    def bytes(self) -> builtins.bytes: ...

    def text(self) -> str: ...

    def json(self, *, max_values: int = 100_000) -> JsonValue: ...

    def verify(self) -> None: ...


class _Publication:
    """A verified-on-read static publication directory."""

    __slots__ = ("_index", "_source")

    def __init__(self, source: _DirectorySource, index: PublicationIndex) -> None:
        self._source = source
        self._index = index

    @property
    def notebook(self) -> _NotebookProvenance:
        return self._index.notebook

    @property
    def producer(self) -> _ProducerProvenance:
        return self._index.producer

    @property
    def variant_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._index.variants))

    def variant(self, name: str) -> PublishedVariant:
        _selection_name(name, "variant")
        try:
            entry = self._index.variants[name]
        except KeyError as error:
            raise _not_found("variant", name, self._index.variants.keys()) from error
        return _PublishedVariant(self, name, entry)

    def describe(self) -> JsonObject:
        """Return public publication metadata detached from cache references."""

        variants: JsonObject = {}
        for variant_name in sorted(self._index.variants):
            variant = self._index.variants[variant_name]
            outputs: JsonObject = {}
            for output_name in sorted(variant.outputs):
                output = variant.outputs[output_name]
                formats: JsonObject = {}
                for format_name in sorted(output.formats):
                    entry = output.formats[format_name]
                    formats[format_name] = {
                        "format_id": entry.format_id,
                        "media_type": entry.media_type,
                        "metadata": json_object(entry.metadata, "format metadata"),
                    }
                outputs[output_name] = {"formats": formats}
            variants[variant_name] = {
                "controls": json_object(variant.controls, "variant controls"),
                "outputs": outputs,
            }
        return {
            "schema": PUBLICATION_SCHEMA,
            "asset_codec": ASSET_CODEC,
            "notebook": self.notebook.wire(),
            "producer": self.producer.wire(),
            "variants": variants,
        }

    def verify(self) -> int:
        """Verify every format reference and BlobAsset envelope."""

        seen: set[str] = set()
        for _, _, _, entry in self._index.format_entries():
            if entry.asset.key in seen:
                continue
            seen.add(entry.asset.key)
            _verify_index_agreement(self._read_asset(entry.asset), entry)
        return len(seen)

    def _read_entry(self, entry: FormatEntry) -> _BlobAsset:
        asset = self._read_asset(entry.asset)
        _verify_index_agreement(asset, entry)
        return asset

    def _read_asset(self, reference: AssetRef) -> _BlobAsset:
        raw = self._source.read_asset(reference)
        return _decode_blob_asset(raw, reference)


class _PublishedVariant:
    __slots__ = ("_entry", "_name", "_publication")

    def __init__(
        self,
        publication: _Publication,
        name: str,
        entry: VariantEntry,
    ) -> None:
        self._publication = publication
        self._entry = entry
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def controls(self) -> JsonObject:
        return json_object(self._entry.controls, f"variant {self.name!r} controls")

    @property
    def outputs(self) -> tuple[str, ...]:
        return tuple(sorted(self._entry.outputs))

    def output(self, name: str) -> PublishedOutput:
        _selection_name(name, "output")
        try:
            entry = self._entry.outputs[name]
        except KeyError as error:
            raise _not_found("output", name, self._entry.outputs.keys()) from error
        return _PublishedOutput(self._publication, self.name, name, entry)


class _PublishedOutput:
    __slots__ = ("_entry", "_name", "_publication", "_variant")

    def __init__(
        self,
        publication: _Publication,
        variant: str,
        name: str,
        entry: OutputEntry,
    ) -> None:
        self._publication = publication
        self._entry = entry
        self._variant = variant
        self._name = name

    @property
    def variant(self) -> str:
        return self._variant

    @property
    def name(self) -> str:
        return self._name

    @property
    def formats(self) -> tuple[str, ...]:
        return tuple(sorted(self._entry.formats))

    def format(self, name: str) -> PublishedFormat:
        _selection_name(name, "format")
        try:
            entry = self._entry.formats[name]
        except KeyError as error:
            raise _not_found("format", name, self._entry.formats.keys()) from error
        return _PublishedFormat(self._publication, self.variant, self.name, name, entry)


class _PublishedFormat:
    __slots__ = ("_entry", "_name", "_output", "_publication", "_variant")

    def __init__(
        self,
        publication: _Publication,
        variant: str,
        output: str,
        name: str,
        entry: FormatEntry,
    ) -> None:
        self._publication = publication
        self._entry = entry
        self._variant = variant
        self._output = output
        self._name = name

    @property
    def variant(self) -> str:
        return self._variant

    @property
    def output(self) -> str:
        return self._output

    @property
    def name(self) -> str:
        return self._name

    @property
    def format_id(self) -> str:
        return self._entry.format_id

    @property
    def media_type(self) -> str:
        return self._entry.media_type

    @property
    def metadata(self) -> JsonObject:
        return json_object(self._entry.metadata, "format metadata")

    @property
    def filename(self) -> str | None:
        return self._blob().filename

    def bytes(self) -> builtins.bytes:
        return self._blob().data.tobytes()

    def text(self) -> str:
        data = self._blob().data
        try:
            declared = _charset(self.media_type)
        except (LookupError, UnicodeError, ValueError) as error:
            raise PublicationError(
                f"format {self.name!r} declares an invalid text charset"
            ) from error
        if declared is not None and declared.lower() != "utf-8":
            raise PublicationError(
                f"format {self.name!r} declares charset {declared!r}. "
                "use bytes() for explicit decoding"
            )
        try:
            return str(data, "utf-8-sig")
        except (UnicodeError, ValueError) as error:
            raise PublicationError(
                f"format {self.name!r} cannot be decoded as UTF-8 text"
            ) from error

    def json(self, *, max_values: int = 100_000) -> JsonValue:
        _read_limit(max_values, "max_values")
        try:
            return decode_json(
                self._blob().data,
                f"format {self.name!r}",
                max_values=max_values,
            )
        except (TypeError, ValueError) as error:
            raise PublicationError(f"format {self.name!r} does not contain valid JSON") from error

    def verify(self) -> None:
        self._blob()

    def _blob(self) -> _BlobAsset:
        return self._publication._read_entry(self._entry)


def open_publication(
    path: str | Path,
    *,
    max_index_bytes: int = _DEFAULT_MAX_INDEX_BYTES,
    max_asset_bytes: int = _DEFAULT_MAX_ASSET_BYTES,
    max_publication_bytes: int = _DEFAULT_MAX_PUBLICATION_BYTES,
) -> Publication:
    source = _DirectorySource.open(
        path,
        max_index_bytes=max_index_bytes,
        max_asset_bytes=max_asset_bytes,
        max_publication_bytes=max_publication_bytes,
    )
    index_bytes = source.read_index()
    index = PublicationIndex.from_bytes(index_bytes)
    source.verify_closure(index, index_bytes=len(index_bytes))
    return _Publication(source, index)


class _DirectorySource:
    def __init__(
        self,
        root: Path,
        max_index_bytes: int,
        max_asset_bytes: int,
        max_publication_bytes: int,
    ) -> None:
        self.root = root
        self.max_index_bytes = max_index_bytes
        self.max_asset_bytes = max_asset_bytes
        self.max_publication_bytes = max_publication_bytes

    @classmethod
    def open(
        cls,
        path: str | Path,
        *,
        max_index_bytes: int,
        max_asset_bytes: int,
        max_publication_bytes: int,
    ) -> _DirectorySource:
        _read_limit(max_index_bytes, "max_index_bytes")
        _read_limit(max_asset_bytes, "max_asset_bytes")
        _read_limit(max_publication_bytes, "max_publication_bytes")
        try:
            root = Path(path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, TypeError) as error:
            raise PublicationError(f"publication directory is unavailable: {path}") from error
        if not root.is_dir():
            raise PublicationError(f"publication path is not a directory: {path}")
        if not (root / "cache").is_dir():
            raise PublicationError(f"publication cache path is not a directory: {root / 'cache'}")
        return cls(root, max_index_bytes, max_asset_bytes, max_publication_bytes)

    def read_index(self) -> bytes:
        try:
            return read_publication_index(self.root, max_bytes=self.max_index_bytes)
        except SecureReadLimitError as error:
            raise PublicationError(
                f"publication index exceeds the {self.max_index_bytes} byte read limit"
            ) from error
        except SecureFileSizeError as error:
            raise PublicationError("publication index changed while it was read") from error
        except SecureReadError as error:
            raise PublicationError("publication index could not be read securely") from error

    def read_asset(self, reference: AssetRef) -> bytes:
        try:
            data = read_cache_asset(
                self.root,
                reference.key,
                expected_size=reference.size,
                max_bytes=self.max_asset_bytes,
            )
        except SecureReadLimitError as error:
            raise PublicationError(
                f"cache asset {reference.key!r} exceeds the {self.max_asset_bytes} byte read limit"
            ) from error
        except SecureFileSizeError as error:
            raise IntegrityError(
                f"cache asset {reference.key!r} has an unexpected size",
                details={"expected": error.expected_size, "actual": error.actual_size},
            ) from error
        except SecureReadError as error:
            raise PublicationError(
                f"cache asset {reference.key!r} could not be read securely"
            ) from error
        digest = sha256_bytes(data)
        if digest != reference.sha256:
            raise IntegrityError(
                f"cache asset {reference.key!r} failed SHA-256 verification",
                details={"expected": reference.sha256, "actual": digest},
            )
        return data

    def verify_closure(self, index: PublicationIndex, *, index_bytes: int) -> None:
        total = index_bytes
        if total > self.max_publication_bytes:
            raise PublicationError(
                f"publication closure exceeds the {self.max_publication_bytes} byte read limit"
            )
        for asset in index.assets():
            if asset.size > self.max_asset_bytes:
                raise PublicationError(
                    f"cache asset {asset.key!r} exceeds the {self.max_asset_bytes} byte read limit"
                )
            if asset.size > self.max_publication_bytes - total:
                raise PublicationError(
                    f"publication closure exceeds the {self.max_publication_bytes} byte read limit"
                )
            total += asset.size


def _read_limit(value: object, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0 or value > 2**53 - 1:
        raise TypeError(f"{name} must be a positive safe integer")


def _selection_name(name: object, kind: str) -> None:
    if not isinstance(name, str):
        raise TypeError(f"{kind} name must be a string")


def _not_found(kind: str, name: str, available: Collection[str]) -> PublicationError:
    bounded_name, name_truncated = _diagnostic_name(name)
    shown_available, available_count = _available_prefix(available)
    message = _bounded_message(f"publication has no {kind} {bounded_name!r}")
    return _PublicationNotFoundError(
        message,
        details={
            "kind": kind,
            "name": bounded_name,
            "name_truncated": name_truncated,
            "available": shown_available,
            "available_count": available_count,
            "available_truncated": len(shown_available) < available_count,
        },
    )


def _diagnostic_name(name: str) -> tuple[str, bool]:
    pieces: list[tuple[str, int]] = []
    size = 0
    for character in name:
        piece = character if character.isprintable() else repr(character)[1:-1]
        piece_size = len(piece.encode("utf-8"))
        if size + piece_size > _NOT_FOUND_NAME_UTF8_LIMIT:
            marker_size = len(_TRUNCATION_MARKER)
            while pieces and size + marker_size > _NOT_FOUND_NAME_UTF8_LIMIT:
                _, removed_size = pieces.pop()
                size -= removed_size
            pieces.append((_TRUNCATION_MARKER, marker_size))
            return "".join(piece for piece, _ in pieces), True
        pieces.append((piece, piece_size))
        size += piece_size
    return "".join(piece for piece, _ in pieces), False


def _available_prefix(available: Collection[str]) -> tuple[list[str], int]:
    names: list[str] = []
    available_count = 0
    for name in available:
        available_count += 1
        insort(names, name)
        if len(names) > _NOT_FOUND_AVAILABLE_LIMIT:
            names.pop()

    prefix: list[str] = []
    used_bytes = 0
    for name in names:
        if len(prefix) == _NOT_FOUND_AVAILABLE_LIMIT:
            break
        remaining = _NOT_FOUND_AVAILABLE_UTF8_LIMIT - used_bytes
        name_size = _utf8_size_within(name, remaining)
        if name_size is None:
            break
        prefix.append(name)
        used_bytes += name_size
    return prefix, available_count


def _utf8_size_within(value: str, maximum: int) -> int | None:
    size = 0
    for character in value:
        size += len(character.encode("utf-8"))
        if size > maximum:
            return None
    return size


def _bounded_message(message: str) -> str:
    if len(message) <= _NOT_FOUND_MESSAGE_LIMIT:
        return message
    return f"{message[: _NOT_FOUND_MESSAGE_LIMIT - len(_TRUNCATION_MARKER)]}{_TRUNCATION_MARKER}"


def _decode_blob_asset(data: bytes, reference: AssetRef) -> _BlobAsset:
    try:
        value = decode_blob_asset_wire(data, maximum_bytes=reference.size)
    except _BlobAssetFieldLimitError as error:
        if error.field == "filename":
            raise IntegrityError(f"cache asset {reference.key!r} filename is invalid") from error
        raise IntegrityError(
            f"cache asset {reference.key!r} is not a valid BlobAsset envelope"
        ) from error
    except (RecursionError, TypeError, ValueError) as error:
        raise IntegrityError(
            f"cache asset {reference.key!r} is not a valid BlobAsset envelope"
        ) from error
    if not value.media_type:
        raise IntegrityError(f"cache asset {reference.key!r} media_type must be a string")
    if value.filename is not None:
        try:
            validate_portable_basename(value.filename, "BlobAsset.filename")
        except (TypeError, ValueError) as error:
            raise IntegrityError(f"cache asset {reference.key!r} filename is invalid") from error
    if not value.format_id:
        raise IntegrityError(f"cache asset {reference.key!r} format_id must be a string")
    try:
        metadata = decode_json_object(
            value.metadata_json,
            "BlobAsset.metadata.metadata_json",
        )
    except (TypeError, ValueError) as error:
        raise IntegrityError(
            f"cache asset {reference.key!r} metadata must be JSON-compatible"
        ) from error
    return _BlobAsset(
        data=value.data,
        media_type=value.media_type,
        filename=value.filename,
        format_id=value.format_id,
        metadata=metadata,
    )


def _verify_index_agreement(asset: _BlobAsset, entry: FormatEntry) -> None:
    if asset.media_type != entry.media_type:
        raise IntegrityError(f"cache asset {entry.asset.key!r} media type disagrees with the index")
    if asset.format_id != entry.format_id:
        raise IntegrityError(f"cache asset {entry.asset.key!r} format ID disagrees with the index")
    indexed_metadata = json_object(entry.metadata, "format.metadata")
    if not json_equal(asset.metadata, indexed_metadata):
        raise IntegrityError(f"cache asset {entry.asset.key!r} metadata disagrees with the index")


def _charset(media_type: str) -> str | None:
    for parameter in media_type.split(";")[1:]:
        name, separator, value = parameter.partition("=")
        if separator and name.strip().lower() == "charset":
            charset = value.strip()
            if not charset:
                raise LookupError("media type charset is empty")
            if charset.startswith('"') and charset.endswith('"') and len(charset) >= 2:
                charset = charset[1:-1]
            elif '"' in charset:
                raise LookupError("media type charset has unbalanced quotes")
            if '"' in charset:
                raise LookupError("media type charset contains an unexpected quote")
            if not charset:
                raise LookupError("media type charset is empty")
            return charset
    return None


__all__ = [
    "NotebookProvenance",
    "ProducerProvenance",
    "Publication",
    "PublishedFormat",
    "PublishedOutput",
    "PublishedVariant",
    "open_publication",
]
