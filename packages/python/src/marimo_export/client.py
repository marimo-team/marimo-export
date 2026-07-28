from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import shutil
import sys
import tempfile
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from heapq import nsmallest
from pathlib import Path
from typing import Protocol, runtime_checkable

from marimo_export._diagnostics import safe_diagnostic
from marimo_export._json import (
    JsonObject,
    JsonValue,
    canonical_bytes,
    decode_json,
    json_object,
)
from marimo_export._portable import asset_key_components, validate_asset_key
from marimo_export._remote import (
    BridgeError,
    HttpKernelTransport,
    KernelTransport,
    SessionInfo,
)
from marimo_export.errors import (
    CaptureError,
    IntegrityError,
    ProjectionError,
    PublicationError,
    SelectionError,
    SessionError,
    TransferError,
)
from marimo_export.publication import AssetRef, PublicationIndex
from marimo_export.reader import open_publication
from marimo_export.spec import ExportSpec

_DEFAULT_MAX_ASSET_BYTES = 64 * 1024 * 1024
_DEFAULT_MAX_INDEX_BYTES = 16 * 1024 * 1024
_DEFAULT_MAX_PUBLICATION_BYTES = 512 * 1024 * 1024
_BRIDGE_RESPONSE_HEADROOM_BYTES = 8 * 1024 * 1024
_MAX_SAFE_INTEGER = 2**53 - 1
_MAX_DESCRIPTION_CHARS = 1024
_MAX_PYTHON_TYPE_BYTES = 512
_MAX_TRANSFER_ASSETS = 4096
_SESSION_DIAGNOSTIC_LIMIT = 16
_SESSION_DIAGNOSTIC_CHARS = 512


@dataclass(frozen=True, slots=True)
class BuiltinExporterDescription:
    """Availability of one built-in exporter in the attached kernel."""

    name: str
    format_id: str
    available: bool
    extra: str | None

    def __post_init__(self) -> None:
        _description_string(self.name, "name")
        _description_string(self.format_id, "format_id")
        if not isinstance(self.available, bool):
            raise TypeError("available must be a boolean")
        _description_optional_string(self.extra, "extra")

    def to_dict(self) -> JsonObject:
        return {
            "name": self.name,
            "format_id": self.format_id,
            "available": self.available,
            "extra": self.extra,
        }


@dataclass(frozen=True, slots=True)
class GlobalDescription:
    """One live notebook global available for output selection."""

    name: str
    python_type: str

    def __post_init__(self) -> None:
        _description_string(self.name, "name")
        _python_type_string(self.python_type, "python_type")

    def to_dict(self) -> JsonObject:
        return {"name": self.name, "python_type": self.python_type}


@dataclass(frozen=True, slots=True)
class CellDescription:
    """One authored notebook cell exposed by session inspection."""

    id: str
    name: str | None
    status: str | None
    has_output: bool
    media_type: str | None

    def __post_init__(self) -> None:
        _description_string(self.id, "id")
        _description_optional_string(self.name, "name")
        _description_optional_string(self.status, "status")
        if not isinstance(self.has_output, bool):
            raise TypeError("has_output must be a boolean")
        _description_optional_string(self.media_type, "media_type")

    def to_dict(self) -> JsonObject:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "has_output": self.has_output,
            "media_type": self.media_type,
        }


@dataclass(frozen=True, slots=True, init=False)
class ControlDescription:
    """One named UI control exposed by session inspection."""

    name: str
    type: str
    sensitive: bool
    _value_json: bytes = field(repr=False)
    _domain_json: bytes = field(repr=False)

    def __init__(
        self,
        name: str,
        type: str,
        value: JsonValue,
        *,
        sensitive: bool,
        domain: Mapping[str, JsonValue],
    ) -> None:
        _description_string(name, "name")
        _description_string(type, "type")
        if not isinstance(sensitive, bool):
            raise TypeError("sensitive must be a boolean")
        if sensitive and value is not None:
            raise ValueError("a sensitive control value must be redacted")
        domain_value = json_object(domain, "control domain")
        if sensitive and domain_value:
            raise ValueError("a sensitive control domain must be redacted")
        encoded = canonical_bytes(value)
        encoded_domain = canonical_bytes(domain_value)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "type", type)
        object.__setattr__(self, "sensitive", sensitive)
        object.__setattr__(self, "_value_json", encoded)
        object.__setattr__(self, "_domain_json", encoded_domain)

    @property
    def value(self) -> JsonValue:
        """Return a detached JSON value for the current control state."""

        return decode_json(self._value_json, f"control {self.name!r} value")

    @property
    def domain(self) -> JsonObject:
        """Return detached public bounds and options for the control."""

        value = decode_json(self._domain_json, f"control {self.name!r} domain")
        return json_object(value, f"control {self.name!r} domain")

    def to_dict(self) -> JsonObject:
        return {
            "name": self.name,
            "type": self.type,
            "value": self.value,
            "sensitive": self.sensitive,
            "domain": self.domain,
        }


@dataclass(frozen=True, slots=True)
class CacheSummary:
    """Projection cache outcomes reported by one capture."""

    hits: int
    misses: int
    skipped: int

    def __post_init__(self) -> None:
        _cache_count(self.hits, "hits")
        _cache_count(self.misses, "misses")
        _cache_count(self.skipped, "skipped")

    def to_dict(self) -> JsonObject:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "skipped": self.skipped,
        }


@dataclass(frozen=True, slots=True)
class SessionDescription:
    """Inspectable state exposed by one running marimo notebook session."""

    session_id: str
    filename: str | None
    path: str | None
    document_sha256: str
    marimo_version: str
    marimo_export_version: str
    globals: tuple[GlobalDescription, ...] = ()
    cells: tuple[CellDescription, ...] = ()
    controls: tuple[ControlDescription, ...] = ()
    builtin_exporters: tuple[BuiltinExporterDescription, ...] = ()

    @classmethod
    def _from_wire(cls, session: SessionInfo, value: Mapping[str, object]) -> SessionDescription:
        data = json_object(value, "inspect response")
        if set(data) != {
            "notebook",
            "globals",
            "cells",
            "controls",
            "builtin_exporters",
            "marimo_version",
            "marimo_export_version",
        }:
            raise SessionError(
                "inspect response must contain notebook, globals, cells, controls, "
                "builtin_exporters, marimo_version, and marimo_export_version"
            )
        raw_notebook = data.get("notebook")
        if not isinstance(raw_notebook, Mapping):
            raise SessionError("inspect response.notebook must be an object")
        notebook = json_object(raw_notebook, "inspect response.notebook")
        if set(notebook) != {"filename", "path", "document_sha256"}:
            raise SessionError(
                "inspect response.notebook must contain filename, path, and document_sha256"
            )
        document_sha256 = _session_digest(
            notebook.get("document_sha256"), "inspect response.notebook.document_sha256"
        )

        global_values = _global_descriptions(data.get("globals"), "inspect response.globals")

        return cls(
            session_id=session.id,
            filename=_optional_string(notebook.get("filename"), "notebook.filename")
            or session.filename,
            path=_optional_string(notebook.get("path"), "notebook.path") or session.path,
            document_sha256=document_sha256,
            marimo_version=_session_string(
                data.get("marimo_version"), "inspect response.marimo_version"
            ),
            marimo_export_version=_session_string(
                data.get("marimo_export_version"),
                "inspect response.marimo_export_version",
            ),
            globals=global_values,
            cells=_cell_descriptions(data.get("cells", []), "inspect response.cells"),
            controls=_control_descriptions(data.get("controls", []), "inspect response.controls"),
            builtin_exporters=_builtin_exporter_descriptions(
                data.get("builtin_exporters"),
                "inspect response.builtin_exporters",
            ),
        )

    def to_dict(self) -> JsonObject:
        return json_object(
            {
                "session_id": self.session_id,
                "filename": self.filename,
                "path": self.path,
                "document_sha256": self.document_sha256,
                "marimo_version": self.marimo_version,
                "marimo_export_version": self.marimo_export_version,
                "globals": [global_value.to_dict() for global_value in self.globals],
                "cells": [cell.to_dict() for cell in self.cells],
                "controls": [control.to_dict() for control in self.controls],
                "builtin_exporters": [exporter.to_dict() for exporter in self.builtin_exporters],
            },
            "session description",
        )


@dataclass(frozen=True, slots=True)
class CaptureResult:
    """Summary of a publication committed by :meth:`Session.capture`."""

    path: Path
    session_id: str
    variants: tuple[str, ...]
    outputs: tuple[str, ...]
    assets: int
    bytes_transferred: int
    cache: CacheSummary

    def to_dict(self) -> JsonObject:
        return json_object(
            {
                "path": str(self.path),
                "session_id": self.session_id,
                "variants": list(self.variants),
                "outputs": list(self.outputs),
                "assets": self.assets,
                "bytes_transferred": self.bytes_transferred,
                "cache": self.cache.to_dict(),
            },
            "capture result",
        )


@dataclass(frozen=True, slots=True)
class _TransferAsset:
    key: str
    sha256: str
    size: int
    url: str


@runtime_checkable
class Session(Protocol):
    """Borrowed handle to one running notebook session."""

    @property
    def id(self) -> str: ...

    @property
    def filename(self) -> str | None: ...

    @property
    def path(self) -> str | None: ...

    def inspect(self) -> SessionDescription: ...

    def capture(
        self,
        *,
        spec: ExportSpec | str | os.PathLike[str] | Mapping[str, object],
        into: str | os.PathLike[str],
        replace: bool = False,
    ) -> CaptureResult: ...


class Client:
    """Connect to a user-managed marimo server.

    The client selects existing sessions. Closing it releases local transport
    resources and leaves every marimo session running.
    """

    def __init__(
        self,
        server_url: str,
        *,
        access_token: str | None = None,
        server_token: str | None = None,
        timeout: float = 300.0,
        max_index_bytes: int = _DEFAULT_MAX_INDEX_BYTES,
        max_asset_bytes: int = _DEFAULT_MAX_ASSET_BYTES,
        max_publication_bytes: int = _DEFAULT_MAX_PUBLICATION_BYTES,
    ) -> None:
        self._max_index_bytes = _positive_byte_limit(
            max_index_bytes,
            "max_index_bytes",
        )
        self._max_asset_bytes = _positive_byte_limit(
            max_asset_bytes,
            "max_asset_bytes",
        )
        self._max_publication_bytes = _positive_byte_limit(
            max_publication_bytes,
            "max_publication_bytes",
        )
        bridge_response_bytes = _bridge_response_limit(self._max_index_bytes)
        transport = HttpKernelTransport(
            server_url,
            access_token=access_token,
            server_token=server_token,
            timeout=timeout,
            maximum_event_bytes=bridge_response_bytes,
            maximum_response_bytes=bridge_response_bytes,
        )
        self._transport: KernelTransport = transport
        credential_values = getattr(transport, "_credential_values", ())
        self._diagnostic_secrets = tuple(credential_values)
        self._closed = False

    @classmethod
    def _from_transport(
        cls,
        transport: KernelTransport,
        *,
        max_index_bytes: int = _DEFAULT_MAX_INDEX_BYTES,
        max_asset_bytes: int = _DEFAULT_MAX_ASSET_BYTES,
        max_publication_bytes: int = _DEFAULT_MAX_PUBLICATION_BYTES,
    ) -> Client:
        client = cls.__new__(cls)
        client._transport = transport
        client._max_index_bytes = _positive_byte_limit(
            max_index_bytes,
            "max_index_bytes",
        )
        client._max_asset_bytes = _positive_byte_limit(
            max_asset_bytes,
            "max_asset_bytes",
        )
        client._max_publication_bytes = _positive_byte_limit(
            max_publication_bytes,
            "max_publication_bytes",
        )
        client._diagnostic_secrets = ()
        client._closed = False
        return client

    def __enter__(self) -> Client:
        self._ensure_open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self._transport, "close", None)
        if callable(close):
            close()

    def session(self, session_id: str | None = None) -> Session:
        """Select an existing session.

        Omitting ``session_id`` requires exactly one active session.
        """

        if session_id is not None and (
            not isinstance(session_id, str)
            or not session_id
            or len(session_id) > 1024
            or any(ord(character) < 32 or ord(character) == 127 for character in session_id)
        ):
            raise TypeError("session_id must be a non-empty marimo session ID or None")
        sessions = self.sessions()
        details = _session_diagnostics(sessions, secrets=self._diagnostic_secrets)
        if session_id is not None:
            for session in sessions:
                if session.id == session_id:
                    return session
            raise SessionError(
                safe_diagnostic(
                    "marimo session ",
                    repr(session_id),
                    " is not active on this server",
                    secrets=self._diagnostic_secrets,
                ),
                details=details,
            )
        if not sessions:
            raise SessionError(
                "the marimo server has no active notebook sessions",
                details=details,
            )
        if len(sessions) != 1:
            raise SessionError(
                "the marimo server has multiple active sessions. Pass a session ID",
                details=details,
            )
        return sessions[0]

    def sessions(self) -> tuple[Session, ...]:
        """Return the active sessions without inspecting notebook state."""

        self._ensure_open()
        return tuple(_Session(self, info) for info in self._transport.list_sessions())

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("client is closed")


class _Session:
    """A borrowed running marimo notebook session."""

    def __init__(self, client: Client, info: SessionInfo) -> None:
        self._client = client
        self._info = info

    @property
    def _transport(self) -> KernelTransport:
        self._client._ensure_open()
        return self._client._transport

    @property
    def id(self) -> str:
        return self._info.id

    @property
    def filename(self) -> str | None:
        return self._info.filename

    @property
    def path(self) -> str | None:
        return self._info.path

    def inspect(self) -> SessionDescription:
        """Describe selectable values, cell outputs, and UI controls."""

        try:
            value = self._transport.invoke(self.id, "inspect", {})
        except BridgeError as error:
            raise SessionError(str(error), details=error.details) from error
        try:
            return SessionDescription._from_wire(self._info, value)
        except SessionError:
            raise
        except (TypeError, ValueError) as error:
            raise SessionError("marimo returned an invalid inspect response") from error

    def capture(
        self,
        *,
        spec: ExportSpec | str | os.PathLike[str] | Mapping[str, object],
        into: str | os.PathLike[str],
        replace: bool = False,
    ) -> CaptureResult:
        """Capture one export specification into a local publication directory."""

        self._client._ensure_open()
        if not isinstance(replace, bool):
            raise TypeError("replace must be a boolean")
        export_spec = _coerce_spec(spec)
        destination = _prepare_destination(
            into,
            replace=replace,
            max_index_bytes=self._client._max_index_bytes,
            max_asset_bytes=self._client._max_asset_bytes,
            max_publication_bytes=self._client._max_publication_bytes,
        )

        receipt: JsonObject | None = None
        ticket: str | None = None
        primary_error: BaseException | None = None
        try:
            receipt = _invoke_capture(
                self._transport,
                self.id,
                export_spec,
                max_index_bytes=self._client._max_index_bytes,
                max_publication_bytes=self._client._max_publication_bytes,
            )
            ticket = _required_string(receipt, "ticket", "capture response")
            expected_document_sha256 = _capture_document_sha256(receipt)
            current_document_sha256 = self.inspect().document_sha256
            if current_document_sha256 != expected_document_sha256:
                raise CaptureError(
                    "the notebook document changed after capture",
                    details={
                        "captured": expected_document_sha256,
                        "current": current_document_sha256,
                    },
                )
            return _materialize_publication(
                self._transport,
                self.id,
                receipt,
                destination,
                replace=replace,
                max_index_bytes=self._client._max_index_bytes,
                max_asset_bytes=self._client._max_asset_bytes,
                max_publication_bytes=self._client._max_publication_bytes,
            )
        except BaseException as error:
            primary_error = error
            raise
        finally:
            if ticket is not None:
                try:
                    self._transport.invoke(self.id, "release", {"ticket": ticket})
                except BaseException as cleanup_error:
                    if primary_error is None:
                        if not isinstance(cleanup_error, Exception):
                            raise
                        raise TransferError(
                            "publication was committed but transfer cleanup failed",
                            details={"committed": True, "path": str(destination)},
                        ) from cleanup_error


def capture(
    server_url: str,
    *,
    spec: ExportSpec | str | os.PathLike[str] | Mapping[str, object],
    into: str | os.PathLike[str],
    session: str | None = None,
    replace: bool = False,
    access_token: str | None = None,
    server_token: str | None = None,
    timeout: float = 300.0,
    max_index_bytes: int = _DEFAULT_MAX_INDEX_BYTES,
    max_asset_bytes: int = _DEFAULT_MAX_ASSET_BYTES,
    max_publication_bytes: int = _DEFAULT_MAX_PUBLICATION_BYTES,
) -> CaptureResult:
    """Capture selected values from an existing marimo session."""

    if not isinstance(replace, bool):
        raise TypeError("replace must be a boolean")
    export_spec = _coerce_spec(spec)
    destination = _prepare_destination(
        into,
        replace=replace,
        max_index_bytes=max_index_bytes,
        max_asset_bytes=max_asset_bytes,
        max_publication_bytes=max_publication_bytes,
    )
    with Client(
        server_url,
        access_token=access_token,
        server_token=server_token,
        timeout=timeout,
        max_index_bytes=max_index_bytes,
        max_asset_bytes=max_asset_bytes,
        max_publication_bytes=max_publication_bytes,
    ) as client:
        return client.session(session).capture(
            spec=export_spec,
            into=destination,
            replace=replace,
        )


def _coerce_spec(
    value: ExportSpec | str | os.PathLike[str] | Mapping[str, object],
) -> ExportSpec:
    if isinstance(value, ExportSpec):
        return value
    if isinstance(value, Mapping):
        return ExportSpec.from_value(value)
    if isinstance(value, (str, os.PathLike)):
        return ExportSpec.from_file(value)
    return ExportSpec.from_value(value)


def _session_diagnostics(
    sessions: tuple[Session, ...],
    *,
    secrets: tuple[str, ...],
) -> JsonObject:
    prefix = nsmallest(
        _SESSION_DIAGNOSTIC_LIMIT,
        sessions,
        key=lambda session: (session.id, session.filename or "", session.path or ""),
    )
    return {
        "sessions": [
            {
                "id": safe_diagnostic(
                    session.id,
                    secrets=secrets,
                    maximum_chars=_SESSION_DIAGNOSTIC_CHARS,
                ),
                "filename": _diagnostic_optional(session.filename, secrets=secrets),
                "path": _diagnostic_optional(session.path, secrets=secrets),
            }
            for session in prefix
        ],
        "session_count": len(sessions),
        "sessions_truncated": len(sessions) > len(prefix),
    }


def _diagnostic_optional(value: str | None, *, secrets: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    return safe_diagnostic(
        value,
        secrets=secrets,
        maximum_chars=_SESSION_DIAGNOSTIC_CHARS,
    )


def _prepare_destination(
    value: str | os.PathLike[str],
    *,
    replace: bool,
    max_index_bytes: int = _DEFAULT_MAX_INDEX_BYTES,
    max_asset_bytes: int = _DEFAULT_MAX_ASSET_BYTES,
    max_publication_bytes: int = _DEFAULT_MAX_PUBLICATION_BYTES,
) -> Path:
    destination = Path(value).expanduser()
    if destination.name in {"", ".", ".."}:
        raise ValueError("publication destination must name a directory")
    destination = destination.absolute()
    if destination.is_symlink():
        raise FileExistsError(f"publication destination is a symlink: {destination}")
    if destination.exists():
        if not destination.is_dir():
            raise FileExistsError(f"publication destination is not a directory: {destination}")
        if not replace:
            raise FileExistsError(f"publication destination exists: {destination}")
        _verify_replacement_target(
            destination,
            max_index_bytes=max_index_bytes,
            max_asset_bytes=max_asset_bytes,
            max_publication_bytes=max_publication_bytes,
        )
    return destination


def _invoke_capture(
    transport: KernelTransport,
    session_id: str,
    spec: ExportSpec,
    *,
    max_index_bytes: int,
    max_publication_bytes: int,
) -> JsonObject:
    try:
        value = transport.invoke(
            session_id,
            "capture",
            {
                "spec": spec.to_value(),
                "maximum_index_bytes": max_index_bytes,
                "maximum_publication_bytes": max_publication_bytes,
            },
        )
    except BridgeError as error:
        remote_code = getattr(error, "remote_code", "")
        if remote_code.startswith("session"):
            raise SessionError(str(error), details=error.details) from error
        error_type: type[CaptureError]
        if remote_code.startswith("selection"):
            error_type = SelectionError
        elif remote_code.startswith("projection"):
            error_type = ProjectionError
        elif remote_code.startswith("transfer"):
            error_type = TransferError
        else:
            error_type = CaptureError
        raise error_type(str(error), details=error.details) from error
    try:
        return json_object(value, "capture response")
    except (TypeError, ValueError) as error:
        raise CaptureError(f"marimo returned an invalid capture response: {error}") from error


def _capture_document_sha256(receipt: Mapping[str, object]) -> str:
    try:
        index = PublicationIndex.from_wire(_object_field(receipt, "index"))
    except (CaptureError, PublicationError) as error:
        raise CaptureError("capture returned an invalid publication index") from error
    return index.notebook.document_sha256


def _materialize_publication(
    transport: KernelTransport,
    session_id: str,
    receipt: JsonObject,
    destination: Path,
    *,
    replace: bool,
    max_index_bytes: int,
    max_asset_bytes: int,
    max_publication_bytes: int,
) -> CaptureResult:
    unknown = set(receipt) - {
        "ticket",
        "expires_at_ms",
        "index",
        "assets",
        "cache",
    }
    missing = {"ticket", "index", "assets", "cache"} - set(receipt)
    if unknown or missing:
        problems: list[str] = []
        if unknown:
            problems.append(f"unknown fields: {', '.join(sorted(unknown))}")
        if missing:
            problems.append(f"missing fields: {', '.join(sorted(missing))}")
        raise CaptureError(f"invalid capture response ({'. '.join(problems)})")
    if "expires_at_ms" in receipt:
        _required_size(receipt, "expires_at_ms", "capture response")

    try:
        index_value = _object_field(receipt, "index")
    except CaptureError:
        raise
    except (TypeError, ValueError) as error:
        raise CaptureError("capture returned an invalid publication index") from error
    try:
        index = PublicationIndex.from_wire(index_value)
    except PublicationError as error:
        raise CaptureError(f"capture returned an invalid publication index: {error}") from error
    index_bytes = index.to_bytes()
    if len(index_bytes) > max_index_bytes:
        raise CaptureError(
            f"capture index exceeds the {max_index_bytes}-byte limit",
            details={"size": len(index_bytes), "limit": max_index_bytes},
        )

    assets = _decode_transfer_assets(receipt.get("assets"))
    for asset in assets:
        if asset.size > max_asset_bytes:
            raise TransferError(
                f"cache asset {asset.key!r} exceeds the {max_asset_bytes}-byte capture limit",
                details={"asset": asset.key, "size": asset.size, "limit": max_asset_bytes},
            )
    expected = {asset.key: (asset.sha256, asset.size) for asset in index.assets()}
    received = {asset.key: asset for asset in assets}
    if set(received) != set(expected):
        raise TransferError("capture receipt assets do not match the publication index")
    for key, reference in expected.items():
        asset = received[key]
        if (asset.sha256, asset.size) != reference:
            raise TransferError(f"capture receipt for cache asset {key!r} does not match the index")
    _validate_publication_size(
        len(index_bytes),
        assets,
        max_publication_bytes,
    )

    cache_summary = _decode_cache_summary(receipt.get("cache"))

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=destination.parent))
    committed = False
    bytes_transferred = 0
    try:
        cache_root = staging / "cache"
        cache_root.mkdir()
        synced_directories: set[Path] = {cache_root}
        for asset in assets:
            payload = transport.download_asset(
                session_id,
                asset.url,
                maximum_bytes=min(asset.size, max_asset_bytes),
            )
            _verify_asset(payload, asset)
            target, directories = _cache_target_and_directories(cache_root, asset.key)
            synced_directories.update(directories)
            _write_file(target, payload)
            bytes_transferred += len(payload)

        _write_file(staging / "index.json", index_bytes)
        open_publication(
            staging,
            max_index_bytes=max_index_bytes,
            max_asset_bytes=max_asset_bytes,
            max_publication_bytes=max_publication_bytes,
        ).verify()
        for directory in sorted(
            synced_directories,
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            _sync_directory(directory)
        _sync_directory(staging)
        _commit_directory(
            staging,
            destination,
            replace=replace,
            max_index_bytes=max_index_bytes,
            max_asset_bytes=max_asset_bytes,
            max_publication_bytes=max_publication_bytes,
        )
        committed = True
    finally:
        if not committed:
            shutil.rmtree(staging, ignore_errors=True)

    variants = tuple(index.variants)
    outputs = tuple(
        dict.fromkeys(
            output_name for variant in index.variants.values() for output_name in variant.outputs
        )
    )
    return CaptureResult(
        path=destination,
        session_id=session_id,
        variants=variants,
        outputs=outputs,
        assets=len(assets),
        bytes_transferred=bytes_transferred,
        cache=cache_summary,
    )


def _decode_transfer_assets(value: object) -> tuple[_TransferAsset, ...]:
    if not isinstance(value, list):
        raise TransferError("capture response assets must be a list")
    if len(value) > _MAX_TRANSFER_ASSETS:
        raise TransferError(
            f"capture response may contain at most {_MAX_TRANSFER_ASSETS} cache assets"
        )
    assets: list[_TransferAsset] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise TransferError(f"capture response asset {index} must be an object")
        try:
            item = json_object(item, f"capture response asset {index}")
        except (TypeError, ValueError) as error:
            raise TransferError(f"capture response asset {index} is invalid") from error
        if set(item) != {"key", "sha256", "size", "url"}:
            raise TransferError(
                f"capture response asset {index} must contain key, sha256, size, and url"
            )
        key = _required_string(item, "key", f"capture response asset {index}")
        _validate_cache_key(key)
        sha256 = _required_digest(item, "sha256", f"capture response asset {index}")
        size = _required_size(item, "size", f"capture response asset {index}")
        url = _required_string(item, "url", f"capture response asset {index}")
        if key in seen:
            raise TransferError(f"capture response repeats cache asset {key!r}")
        seen.add(key)
        assets.append(_TransferAsset(key=key, sha256=sha256, size=size, url=url))
    return tuple(assets)


def _validate_publication_size(
    index_size: int,
    assets: tuple[_TransferAsset, ...],
    max_publication_bytes: int,
) -> None:
    if index_size > max_publication_bytes:
        raise TransferError(
            "publication exceeds max_publication_bytes",
            details={
                "limit": max_publication_bytes,
                "accounted_bytes": 0,
                "index_size": index_size,
            },
        )
    total = index_size
    for asset in assets:
        if asset.size > max_publication_bytes - total:
            raise TransferError(
                "publication exceeds max_publication_bytes",
                details={
                    "limit": max_publication_bytes,
                    "accounted_bytes": total,
                    "asset": asset.key,
                    "asset_size": asset.size,
                },
            )
        total += asset.size


def _verify_asset(payload: bytes, asset: _TransferAsset) -> None:
    if len(payload) != asset.size:
        raise IntegrityError(
            f"cache asset {asset.key!r} has size {len(payload)}, expected {asset.size}"
        )
    digest = hashlib.sha256(payload).hexdigest()
    if digest != asset.sha256:
        raise IntegrityError(
            f"cache asset {asset.key!r} has SHA-256 {digest}, expected {asset.sha256}"
        )


def _cache_path(cache_root: Path, key: str) -> Path:
    return cache_root.joinpath(*_cache_key_components(key))


def _validate_cache_key(key: str) -> None:
    try:
        validate_asset_key(key, "cache asset key")
    except (TypeError, ValueError) as error:
        raise TransferError(str(error)) from error


def _cache_key_components(key: str) -> tuple[str, ...]:
    try:
        return asset_key_components(key, "cache asset key")
    except (TypeError, ValueError) as error:
        raise TransferError(str(error)) from error


def _write_file(path: Path, value: bytes) -> None:
    with path.open("xb") as file:
        file.write(value)
        file.flush()
        os.fsync(file.fileno())


def _sync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _commit_directory(
    staging: Path,
    destination: Path,
    *,
    replace: bool,
    max_index_bytes: int = _DEFAULT_MAX_INDEX_BYTES,
    max_asset_bytes: int = _DEFAULT_MAX_ASSET_BYTES,
    max_publication_bytes: int = _DEFAULT_MAX_PUBLICATION_BYTES,
) -> None:
    if not destination.exists():
        _commit_new_directory(staging, destination)
        return
    if not replace:
        raise FileExistsError(f"publication destination exists: {destination}")
    if destination.is_symlink() or not destination.is_dir():
        raise FileExistsError(f"publication destination is not a directory: {destination}")
    _commit_replacement(
        staging,
        destination,
        max_index_bytes=max_index_bytes,
        max_asset_bytes=max_asset_bytes,
        max_publication_bytes=max_publication_bytes,
    )


def _commit_new_directory(staging: Path, destination: Path) -> None:
    _rename_directory_noreplace(staging, destination)
    _sync_directory_best_effort(destination.parent)


def _commit_replacement(
    staging: Path,
    destination: Path,
    *,
    max_index_bytes: int,
    max_asset_bytes: int,
    max_publication_bytes: int,
) -> None:
    _verify_replacement_target(
        destination,
        max_index_bytes=max_index_bytes,
        max_asset_bytes=max_asset_bytes,
        max_publication_bytes=max_publication_bytes,
    )
    index = PublicationIndex.from_bytes((staging / "index.json").read_bytes())
    cache_root = destination / "cache"
    if cache_root.is_symlink() or not cache_root.is_dir():
        raise FileExistsError(f"publication cache is not a directory: {cache_root}")

    synced_directories: set[Path] = {cache_root}
    for reference in index.assets():
        source = _cache_path(staging / "cache", reference.key)
        target, directories = _cache_target_and_directories(cache_root, reference.key)
        synced_directories.update(directories)
        if target.exists() or target.is_symlink():
            _verify_existing_cache_asset(target, reference)
            continue
        try:
            os.link(source, target)
        except FileExistsError:
            _verify_existing_cache_asset(target, reference)

    for reference in index.assets():
        _verify_existing_cache_asset(
            _cache_path(cache_root, reference.key),
            reference,
        )

    for directory in sorted(synced_directories, key=lambda path: len(path.parts), reverse=True):
        _sync_directory(directory)

    temporary_index = destination / f".index.json.tmp-{uuid.uuid4().hex}"
    committed = False
    try:
        os.link(staging / "index.json", temporary_index)
        _sync_directory(destination)
        os.replace(temporary_index, destination / "index.json")
        committed = True
    finally:
        if not committed:
            with suppress(OSError):
                temporary_index.unlink()
    _sync_directory_best_effort(destination)
    with suppress(OSError):
        shutil.rmtree(staging)


def _verify_replacement_target(
    destination: Path,
    *,
    max_index_bytes: int,
    max_asset_bytes: int,
    max_publication_bytes: int,
) -> None:
    try:
        open_publication(
            destination,
            max_index_bytes=max_index_bytes,
            max_asset_bytes=max_asset_bytes,
            max_publication_bytes=max_publication_bytes,
        ).verify()
    except PublicationError as error:
        raise PublicationError(f"replacement target is not a valid publication: {error}") from error


def _cache_target_and_directories(
    cache_root: Path,
    key: str,
) -> tuple[Path, tuple[Path, ...]]:
    components = _cache_key_components(key)
    current = cache_root
    directories = [cache_root]
    for part in components[:-1]:
        current /= part
        if current.exists():
            if current.is_symlink() or not current.is_dir():
                raise FileExistsError(f"cache path component is not a directory: {current}")
        else:
            current.mkdir()
        directories.append(current)
    target = current / components[-1]
    try:
        target.parent.resolve(strict=True).relative_to(cache_root.resolve(strict=True))
    except (OSError, RuntimeError, ValueError) as error:
        raise TransferError(
            f"cache asset path resolves outside the publication: {key!r}"
        ) from error
    return target, tuple(directories)


def _verify_existing_cache_asset(path: Path, reference: AssetRef) -> None:
    if path.is_symlink() or not path.is_file():
        raise TransferError(f"cache asset path is not a regular file: {reference.key!r}")
    try:
        size = path.stat().st_size
        digest = _file_sha256(path) if size == reference.size else None
    except OSError as error:
        raise TransferError(f"could not inspect existing cache asset {reference.key!r}") from error
    if size != reference.size or digest != reference.sha256:
        raise TransferError(
            f"cache key {reference.key!r} already contains different bytes",
            details={
                "key": reference.key,
                "existing_size": size,
                "expected_size": reference.size,
                "existing_sha256": digest,
                "expected_sha256": reference.sha256,
            },
        )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _rename_directory_noreplace(source: Path, destination: Path) -> None:
    if os.name == "nt":
        os.rename(source, destination)
        return
    if sys.platform == "darwin":
        _renamex_noreplace(source, destination)
        return
    if sys.platform.startswith("linux"):
        _renameat2_noreplace(source, destination)
        return
    raise OSError(
        errno.ENOTSUP,
        "atomic no-replace directory rename is unavailable on this platform",
        str(destination),
    )


def _renamex_noreplace(source: Path, destination: Path) -> None:
    renamex = ctypes.CDLL(None, use_errno=True).renamex_np
    result = renamex(os.fsencode(source), os.fsencode(destination), 0x00000004)
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def _renameat2_noreplace(source: Path, destination: Path) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    try:
        renameat2 = library.renameat2
    except AttributeError as error:
        raise OSError(
            errno.ENOTSUP,
            "atomic no-replace directory rename is unavailable on this system",
            str(destination),
        ) from error
    result = renameat2(
        -100,
        os.fsencode(source),
        -100,
        os.fsencode(destination),
        0x00000001,
    )
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def _sync_directory_best_effort(path: Path) -> None:
    with suppress(OSError):
        _sync_directory(path)


def _object_field(value: Mapping[str, object], key: str) -> JsonObject:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise CaptureError(f"{key} must be an object")
    return json_object(item, key)


def _required_string(value: Mapping[str, object], key: str, path: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise CaptureError(f"{path}.{key} must be a non-empty string")
    return item


def _optional_string(value: object, path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise SessionError(f"{path} must be a non-empty string or null")
    return value


def _session_digest(value: object, path: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SessionError(f"{path} must be a lowercase SHA-256 digest")
    return value


def _required_digest(value: Mapping[str, object], key: str, path: str) -> str:
    digest = _required_string(value, key, path)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise TransferError(f"{path}.{key} must be a lowercase SHA-256 digest")
    return digest


def _required_size(value: Mapping[str, object], key: str, path: str) -> int:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, int) or item < 0 or item > _MAX_SAFE_INTEGER:
        raise TransferError(f"{path}.{key} must be a non-negative JavaScript-safe integer")
    return item


def _decode_cache_summary(value: object) -> CacheSummary:
    if not isinstance(value, Mapping):
        raise CaptureError("capture response cache summary must be an object")
    try:
        data = json_object(value, "capture response.cache")
    except (TypeError, ValueError) as error:
        raise CaptureError("capture response cache summary is invalid") from error
    if set(data) != {"hits", "misses", "skipped"}:
        raise CaptureError("capture response.cache must contain hits, misses, and skipped")
    return CacheSummary(
        hits=_capture_cache_count(data.get("hits"), "capture response.cache.hits"),
        misses=_capture_cache_count(data.get("misses"), "capture response.cache.misses"),
        skipped=_capture_cache_count(data.get("skipped"), "capture response.cache.skipped"),
    )


def _capture_cache_count(value: object, path: str) -> int:
    try:
        return _cache_count(value, path)
    except (TypeError, ValueError) as error:
        raise CaptureError(f"{path} must be a non-negative JavaScript-safe integer") from error


def _cache_count(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{path} must be an integer")
    if value < 0 or value > _MAX_SAFE_INTEGER:
        raise ValueError(f"{path} must be a non-negative JavaScript-safe integer")
    return value


def _positive_byte_limit(value: int, path: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > _MAX_SAFE_INTEGER
    ):
        raise ValueError(f"{path} must be a positive JavaScript-safe integer")
    return value


def _bridge_response_limit(maximum_index_bytes: int) -> int:
    return maximum_index_bytes * 2 + _BRIDGE_RESPONSE_HEADROOM_BYTES


def _description_string(value: object, path: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_DESCRIPTION_CHARS
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise TypeError(
            f"{path} must be a non-empty string of at most {_MAX_DESCRIPTION_CHARS} "
            "characters without surrounding whitespace or control characters"
        )
    return value


def _python_type_string(value: object, path: str) -> str:
    result = _description_string(value, path)
    if len(result.encode("utf-8")) > _MAX_PYTHON_TYPE_BYTES:
        raise TypeError(f"{path} must be at most {_MAX_PYTHON_TYPE_BYTES} UTF-8 bytes")
    return result


def _description_optional_string(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _description_string(value, path)


def _session_string(value: object, path: str) -> str:
    try:
        return _description_string(value, path)
    except TypeError as error:
        raise SessionError(f"{path} must be a non-empty string") from error


def _cell_descriptions(value: object, path: str) -> tuple[CellDescription, ...]:
    if not isinstance(value, list):
        raise SessionError(f"{path} must be a list")
    result: list[CellDescription] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise SessionError(f"{path}[{index}] must be an object")
        item_path = f"{path}[{index}]"
        data = json_object(item, item_path)
        if set(data) != {"id", "name", "status", "has_output", "media_type"}:
            raise SessionError(
                f"{item_path} must contain id, name, status, has_output, and media_type"
            )
        has_output = data.get("has_output")
        if not isinstance(has_output, bool):
            raise SessionError(f"{item_path}.has_output must be a boolean")
        result.append(
            CellDescription(
                id=_session_string(data.get("id"), f"{item_path}.id"),
                name=_optional_string(data.get("name"), f"{item_path}.name"),
                status=_optional_string(data.get("status"), f"{item_path}.status"),
                has_output=has_output,
                media_type=_optional_string(data.get("media_type"), f"{item_path}.media_type"),
            )
        )
    return tuple(result)


def _control_descriptions(value: object, path: str) -> tuple[ControlDescription, ...]:
    if not isinstance(value, list):
        raise SessionError(f"{path} must be a list")
    result: list[ControlDescription] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise SessionError(f"{path}[{index}] must be an object")
        item_path = f"{path}[{index}]"
        data = json_object(item, item_path)
        if set(data) != {"name", "type", "value", "sensitive", "domain"}:
            raise SessionError(f"{item_path} must contain name, type, value, sensitive, and domain")
        sensitive = data.get("sensitive")
        if not isinstance(sensitive, bool):
            raise SessionError(f"{item_path}.sensitive must be a boolean")
        result.append(
            ControlDescription(
                name=_session_string(data.get("name"), f"{item_path}.name"),
                type=_session_string(data.get("type"), f"{item_path}.type"),
                value=data["value"],
                sensitive=sensitive,
                domain=json_object(data["domain"], f"{item_path}.domain"),
            )
        )
    return tuple(result)


def _builtin_exporter_descriptions(
    value: object,
    path: str,
) -> tuple[BuiltinExporterDescription, ...]:
    if not isinstance(value, list):
        raise SessionError(f"{path} must be a list")
    result: list[BuiltinExporterDescription] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise SessionError(f"{path}[{index}] must be an object")
        item_path = f"{path}[{index}]"
        data = json_object(item, item_path)
        if set(data) != {"name", "format_id", "available", "extra"}:
            raise SessionError(f"{item_path} must contain name, format_id, available, and extra")
        available = data.get("available")
        if not isinstance(available, bool):
            raise SessionError(f"{item_path}.available must be a boolean")
        result.append(
            BuiltinExporterDescription(
                name=_session_string(data.get("name"), f"{item_path}.name"),
                format_id=_session_string(data.get("format_id"), f"{item_path}.format_id"),
                available=available,
                extra=_optional_string(data.get("extra"), f"{item_path}.extra"),
            )
        )
    names = [exporter.name for exporter in result]
    if names != sorted(names) or len(names) != len(set(names)):
        raise SessionError(f"{path} must contain unique exporters sorted by name")
    return tuple(result)


def _global_descriptions(value: object, path: str) -> tuple[GlobalDescription, ...]:
    if not isinstance(value, list):
        raise SessionError(f"{path} must be a list")
    result: list[GlobalDescription] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise SessionError(f"{path}[{index}] must be an object")
        item_path = f"{path}[{index}]"
        data = json_object(item, item_path)
        if set(data) != {"name", "python_type"}:
            raise SessionError(f"{item_path} must contain name and python_type")
        result.append(
            GlobalDescription(
                name=_session_string(data.get("name"), f"{item_path}.name"),
                python_type=_session_string(data.get("python_type"), f"{item_path}.python_type"),
            )
        )
    names = [global_value.name for global_value in result]
    if names != sorted(names) or len(names) != len(set(names)):
        raise SessionError(f"{path} must contain unique globals sorted by name")
    return tuple(result)


__all__ = [
    "BuiltinExporterDescription",
    "CacheSummary",
    "CaptureResult",
    "CellDescription",
    "Client",
    "ControlDescription",
    "GlobalDescription",
    "Session",
    "SessionDescription",
    "capture",
]
