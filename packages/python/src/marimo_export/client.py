from __future__ import annotations

import math
import os
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, cast

from marimo_export._json import (
    JsonObject,
    JsonValue,
    canonical_bytes,
    decode_json,
    json_object,
    sha256_bytes,
)
from marimo_export._limits import MAX_EXPORT_ASSET_BYTES, MAX_EXPORT_CLOSURE_BYTES
from marimo_export._remote import BridgeError, HttpKernelTransport, SessionInfo
from marimo_export._writer import WriteResult, preflight_export, write_export
from marimo_export.errors import (
    CodecError,
    CompatibilityError,
    ExecutionError,
    IntegrityError,
    OutputError,
    SessionError,
    SpecError,
    TransportError,
)
from marimo_export.export import (
    ExportIndex,
    OutputCodec,
)
from marimo_export.result import CacheSummary, ExportResult, PhaseTimings, StateRunTimings
from marimo_export.spec import ExportSpec, FrozenJsonObject, FrozenJsonValue, StrPath

_CAPABILITIES = frozenset(
    {
        "asset_transfer",
        "blob_asset",
        "cache_cells",
        "cell_cache_receipts",
        "child_sessions",
        "child_ui_updates",
        "definition_overrides",
        "setup_definition_overrides",
        "synthetic_output_cells",
    }
)
_CODECS = frozenset(
    {
        "marimo.scalar.v1",
        "numpy.npy.v1",
        "apache.arrow.file.v1",
        "marimo.blob-asset.msgpack.v1",
    }
)


@dataclass(frozen=True, slots=True, init=False)
class DefinitionDescription:
    """One notebook definition visible to export preflight."""

    name: str
    cell_id: str
    python_type: str
    kind: Literal["ordinary", "ui"]
    input_mode: Literal["value", "patch"]
    siblings: tuple[str, ...]
    portable_input: bool
    sensitive: bool
    value_available: bool
    _value_bytes: bytes = field(repr=False)
    _domain_bytes: bytes = field(repr=False)

    def __init__(
        self,
        *,
        name: str,
        cell_id: str,
        python_type: str,
        kind: Literal["ordinary", "ui"],
        input_mode: Literal["value", "patch"],
        siblings: tuple[str, ...],
        portable_input: bool,
        sensitive: bool,
        value_available: bool,
        value: JsonValue,
        domain: Mapping[str, JsonValue],
    ) -> None:
        if not isinstance(name, str) or not name.isidentifier():
            raise SessionError("definition name must be a Python identifier")
        if not all(isinstance(item, str) and item for item in (cell_id, python_type)):
            raise SessionError("definition cell_id and python_type must be non-empty strings")
        if kind not in {"ordinary", "ui"}:
            raise SessionError("definition kind must be ordinary or ui")
        if input_mode not in {"value", "patch"}:
            raise SessionError("definition input_mode must be value or patch")
        if kind != "ui" and input_mode != "value":
            raise SessionError("ordinary definitions must use value input mode")
        if (
            not isinstance(siblings, tuple)
            or name not in siblings
            or len(siblings) != len(set(siblings))
        ):
            raise SessionError("definition siblings are invalid")
        for label, flag in (
            ("portable_input", portable_input),
            ("sensitive", sensitive),
            ("value_available", value_available),
        ):
            if not isinstance(flag, bool):
                raise SessionError(f"definition {label} must be a boolean")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "cell_id", cell_id)
        object.__setattr__(self, "python_type", python_type)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "input_mode", input_mode)
        object.__setattr__(self, "siblings", siblings)
        object.__setattr__(self, "portable_input", portable_input)
        object.__setattr__(self, "sensitive", sensitive)
        object.__setattr__(self, "value_available", value_available)
        object.__setattr__(self, "_value_bytes", canonical_bytes(value))
        object.__setattr__(
            self,
            "_domain_bytes",
            canonical_bytes(json_object(domain, "definition domain")),
        )

    @property
    def value(self) -> FrozenJsonValue | None:
        if not self.value_available:
            return None
        return cast(FrozenJsonValue, _freeze(decode_json(self._value_bytes, "definition value")))

    @property
    def domain(self) -> FrozenJsonObject:
        return cast(
            FrozenJsonObject,
            _freeze(decode_json(self._domain_bytes, "definition domain")),
        )

    def to_dict(self) -> JsonObject:
        return {
            "name": self.name,
            "cell_id": self.cell_id,
            "python_type": self.python_type,
            "kind": self.kind,
            "input_mode": self.input_mode,
            "siblings": list(self.siblings),
            "portable_input": self.portable_input,
            "sensitive": self.sensitive,
            "value_available": self.value_available,
            "value": _thaw(self.value) if self.value_available else None,
            "domain": cast(JsonObject, _thaw(self.domain)),
        }


@dataclass(frozen=True, slots=True)
class SessionDescription:
    """Definition-centric description of one selected marimo session."""

    session_id: str
    filename: str | None
    path: str | None
    document_sha256: str
    marimo_version: str
    marimo_export_version: str
    capabilities: tuple[str, ...]
    definitions: tuple[DefinitionDescription, ...]

    def to_dict(self) -> JsonObject:
        return {
            "session_id": self.session_id,
            "filename": self.filename,
            "path": self.path,
            "document_sha256": self.document_sha256,
            "marimo_version": self.marimo_version,
            "marimo_export_version": self.marimo_export_version,
            "capabilities": list(self.capabilities),
            "definitions": [definition.to_dict() for definition in self.definitions],
        }


class Session:
    """A live session handle bound to one `Client`."""

    __slots__ = ("_client", "_info")

    def __init__(self, client: Client, info: SessionInfo) -> None:
        self._client = client
        self._info = info

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
        self._client._require_open()
        try:
            value = self._client._transport.invoke(self.id, "inspect", {})
        except BridgeError as error:
            raise _bridge_error(error) from error
        return _session_description(self._info, value)

    def capture(
        self,
        *,
        spec: ExportSpec,
        output: StrPath,
        replace: bool = False,
    ) -> ExportResult:
        """Create a notebook export from this borrowed session."""

        total_started = time.monotonic()
        self._client._require_open()
        if not isinstance(spec, ExportSpec):
            raise TypeError("spec must be an ExportSpec")
        if not isinstance(replace, bool):
            raise TypeError("replace must be a boolean")
        destination = preflight_export(output, replace=replace)
        captured = self._capture(spec)
        export_started = time.monotonic()
        written = write_export(
            captured.index,
            captured.assets,
            destination,
            replace=replace,
        )
        export_write_seconds = time.monotonic() - export_started
        return _export_result(
            captured,
            written,
            mode="capture",
            session_id=self.id,
            timings=PhaseTimings(
                total_seconds=time.monotonic() - total_started,
                capture_seconds=captured.capture_seconds,
                export_write_seconds=export_write_seconds,
                state_runs=captured.state_run_timings,
            ),
        )

    def _capture(self, spec: ExportSpec) -> _CaptureData:
        """Capture verified export data before local commit."""

        capture_started = time.monotonic()
        self._client._require_open()
        if not isinstance(spec, ExportSpec):
            raise TypeError("spec must be an ExportSpec")
        ticket: str | None = None
        try:
            try:
                response = self._client._transport.invoke(
                    self.id,
                    "capture",
                    {"spec": spec.to_value()},
                )
            except BridgeError as error:
                raise _bridge_error(error) from error
            _exact(
                response,
                {
                    "index",
                    "transfer",
                    "output_cache",
                    "notebook_cache",
                    "state_run_timings",
                },
                "capture response",
            )
            index = ExportIndex.from_value(response.get("index"))
            transfer_value = response.get("transfer")
            ticket = _transfer_ticket(transfer_value)
            transfer = _transfer(transfer_value, index)
            assets: dict[tuple[OutputCodec, str], bytes] = {}
            for item in transfer.assets:
                data = self._client._transport.download_asset(
                    self.id,
                    item.url,
                    item.size,
                )
                if len(data) != item.size:
                    raise IntegrityError("a transferred asset length disagrees with its descriptor")
                if sha256_bytes(data) != item.sha256:
                    raise IntegrityError("a transferred asset digest disagrees with its descriptor")
                assets[(item.codec, item.sha256)] = data
            output_cache = _cache_summary(
                response.get("output_cache"),
                "output cache",
                expected=len(index.states) * len(index.outputs),
            )
            notebook_cache = _cache_summary(
                response.get("notebook_cache"),
                "notebook cache",
            )
            state_run_timings = _state_run_timings(
                response.get("state_run_timings"),
                states=len(index.states),
            )
            live_document_sha256 = self.inspect().document_sha256
            if live_document_sha256 != index.notebook.document_sha256:
                raise ExecutionError(
                    "the parent notebook document changed during capture",
                    code="parent_document_changed",
                    details={
                        "before": index.notebook.document_sha256,
                        "after": live_document_sha256,
                    },
                )
            self._release(ticket)
            ticket = None
            return _CaptureData(
                index=index,
                assets=assets,
                output_cache=output_cache,
                notebook_cache=notebook_cache,
                state_run_timings=state_run_timings,
                capture_seconds=time.monotonic() - capture_started,
            )
        finally:
            if ticket is not None:
                primary = sys.exception()
                try:
                    self._release(ticket)
                except Exception as cleanup_error:
                    if primary is None:
                        raise
                    primary.add_note(
                        f"transfer ticket cleanup also failed: {type(cleanup_error).__name__}"
                    )

    def _release(self, ticket: str) -> None:
        try:
            self._client._transport.invoke(
                self.id,
                "release",
                {"ticket": ticket},
            )
        except BridgeError as error:
            raise _bridge_error(error) from error


class Client:
    """Authenticated client for borrowed marimo edit sessions."""

    __slots__ = ("_closed", "_transport")

    def __init__(
        self,
        server: str,
        *,
        access_token: str | None = None,
        server_token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        _timeout(timeout)
        explicit_access = access_token
        explicit_server = server_token
        if explicit_access is None:
            explicit_access = os.environ.get("MARIMO_EXPORT_ACCESS_TOKEN")
        if explicit_server is None:
            explicit_server = os.environ.get("MARIMO_EXPORT_SERVER_TOKEN")
        self._transport = HttpKernelTransport(
            server,
            access_token=explicit_access,
            server_token=explicit_server,
            timeout=float(timeout),
        )
        self._closed = False

    def __enter__(self) -> Client:
        self._require_open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        del exc_info
        self.close()

    def sessions(self) -> tuple[Session, ...]:
        self._require_open()
        return tuple(Session(self, info) for info in self._transport.list_sessions())

    def session(self, session_id: str | None = None) -> Session:
        sessions = self.sessions()
        if session_id is not None:
            if not isinstance(session_id, str) or not session_id:
                raise TypeError("session_id must be a non-empty string or None")
            for session in sessions:
                if session.id == session_id:
                    return session
            raise SessionError(
                f"session {session_id!r} was not found",
                code="session_not_found",
                details={"session_id": session_id},
            )
        if not sessions:
            raise SessionError("no live marimo session was found", code="session_not_found")
        if len(sessions) != 1:
            raise SessionError(
                "more than one live marimo session is available",
                code="session_ambiguous",
                details={"sessions": [session.id for session in sessions[:16]]},
            )
        return sessions[0]

    def close(self) -> None:
        self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise SessionError("the client is closed", code="client_closed")


def capture(
    server: str,
    *,
    spec: ExportSpec,
    output: StrPath,
    session: str | None = None,
    access_token: str | None = None,
    server_token: str | None = None,
    timeout: float = 30.0,
    replace: bool = False,
) -> ExportResult:
    """Capture one existing marimo session into a static export."""

    with Client(
        server,
        access_token=access_token,
        server_token=server_token,
        timeout=timeout,
    ) as client:
        return client.session(session).capture(
            spec=spec,
            output=output,
            replace=replace,
        )


@dataclass(frozen=True, slots=True)
class _TransferAsset:
    codec: OutputCodec
    sha256: str
    size: int
    url: str


@dataclass(frozen=True, slots=True)
class _Transfer:
    ticket: str
    expires_at_ms: int
    assets: tuple[_TransferAsset, ...]


@dataclass(frozen=True, slots=True)
class _CaptureData:
    index: ExportIndex
    assets: Mapping[tuple[OutputCodec, str], bytes]
    output_cache: CacheSummary
    notebook_cache: CacheSummary
    state_run_timings: StateRunTimings
    capture_seconds: float
    server_start_seconds: float | None = None
    initial_autorun_seconds: float | None = None
    server_shutdown_seconds: float | None = None


def _transfer(value: object, index: ExportIndex) -> _Transfer:
    data = _mapping(value, "capture transfer")
    _exact(data, {"ticket", "expires_at_ms", "assets"}, "capture transfer")
    ticket = data["ticket"]
    expires = data["expires_at_ms"]
    raw_assets = data["assets"]
    if not isinstance(ticket, str) or not ticket:
        raise TransportError("capture transfer ticket is invalid")
    if isinstance(expires, bool) or not isinstance(expires, int) or expires <= 0:
        raise TransportError("capture transfer expiry is invalid")
    if not isinstance(raw_assets, list):
        raise TransportError("capture transfer assets must be a list")
    assets: list[_TransferAsset] = []
    for position, raw in enumerate(raw_assets):
        item = _mapping(raw, f"capture transfer asset {position}")
        _exact(
            item,
            {"codec", "sha256", "size", "url"},
            f"capture transfer asset {position}",
        )
        codec = item["codec"]
        digest = item["sha256"]
        size = item["size"]
        url = item["url"]
        if codec not in _CODECS or codec == "marimo.scalar.v1":
            raise TransportError("capture transfer asset codec is invalid")
        if not isinstance(digest, str) or len(digest) != 64:
            raise TransportError("capture transfer asset digest is invalid")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise TransportError("capture transfer asset size is invalid")
        if not isinstance(url, str) or not url:
            raise TransportError("capture transfer asset URL is invalid")
        assets.append(
            _TransferAsset(
                codec=cast(OutputCodec, codec),
                sha256=digest,
                size=size,
                url=url,
            )
        )
    expected = {(codec, asset.sha256, asset.size) for codec, asset in index.assets()}
    actual = {(asset.codec, asset.sha256, asset.size) for asset in assets}
    if actual != expected or len(actual) != len(assets):
        raise TransportError("capture transfer assets do not match the export")
    if any(asset.size > MAX_EXPORT_ASSET_BYTES for asset in assets):
        raise TransportError("capture transfer asset exceeds the local export limit")
    closure = len(index.to_bytes()) + sum(asset.size for asset in assets)
    if closure > MAX_EXPORT_CLOSURE_BYTES:
        raise TransportError("capture transfer exceeds the local export limit")
    return _Transfer(ticket=ticket, expires_at_ms=expires, assets=tuple(assets))


def _transfer_ticket(value: object) -> str:
    data = _mapping(value, "capture transfer")
    ticket = data.get("ticket")
    if not isinstance(ticket, str) or not ticket:
        raise TransportError("capture transfer ticket is invalid")
    return ticket


def _cache_summary(
    value: object,
    path: str,
    *,
    expected: int | None = None,
) -> CacheSummary:
    data = _mapping(value, path)
    _exact(data, {"hits", "misses"}, path)
    hits = data["hits"]
    misses = data["misses"]
    if isinstance(hits, bool) or not isinstance(hits, int) or hits < 0:
        raise TransportError(f"{path} counts are invalid")
    if isinstance(misses, bool) or not isinstance(misses, int) or misses < 0:
        raise TransportError(f"{path} counts are invalid")
    if expected is not None and hits + misses != expected:
        raise TransportError(f"{path} counts do not cover every state output")
    return CacheSummary(hits=hits, misses=misses)


def _state_run_timings(value: object, *, states: int) -> StateRunTimings:
    path = "state run timings"
    data = _mapping(value, path)
    fields = {
        "states",
        "setup_seconds",
        "dependency_execution_seconds",
        "ui_update_seconds",
        "output_materialization_seconds",
        "cleanup_seconds",
    }
    _exact(data, fields, path)
    if data["states"] != states:
        raise TransportError("state run timing count does not match export states")
    try:
        return StateRunTimings(
            states=cast(int, data["states"]),
            setup_seconds=cast(float, data["setup_seconds"]),
            dependency_execution_seconds=cast(float, data["dependency_execution_seconds"]),
            ui_update_seconds=cast(float, data["ui_update_seconds"]),
            output_materialization_seconds=cast(float, data["output_materialization_seconds"]),
            cleanup_seconds=cast(float, data["cleanup_seconds"]),
        )
    except (TypeError, ValueError) as error:
        raise TransportError("state run timings are invalid") from error


def _session_description(
    info: SessionInfo,
    value: Mapping[str, object],
) -> SessionDescription:
    data = json_object(value, "session inspection")
    _exact(
        data,
        {
            "filename",
            "path",
            "document_sha256",
            "marimo_version",
            "marimo_export_version",
            "capabilities",
            "definitions",
        },
        "session inspection",
    )
    capabilities = data["capabilities"]
    raw_definitions = data["definitions"]
    if not isinstance(capabilities, list) or any(
        not isinstance(name, str) or name not in _CAPABILITIES for name in capabilities
    ):
        raise SessionError("session inspection capabilities are invalid")
    if not isinstance(raw_definitions, list):
        raise SessionError("session inspection definitions must be a list")
    definitions = tuple(
        _definition(item, position) for position, item in enumerate(raw_definitions)
    )
    if tuple(sorted(definition.name for definition in definitions)) != tuple(
        definition.name for definition in definitions
    ):
        raise SessionError("session inspection definitions are not sorted")
    return SessionDescription(
        session_id=info.id,
        filename=_optional_string(data["filename"], "session filename"),
        path=_optional_string(data["path"], "session path"),
        document_sha256=_digest(data["document_sha256"], "session document digest"),
        marimo_version=_string(data["marimo_version"], "marimo version"),
        marimo_export_version=_string(
            data["marimo_export_version"],
            "marimo-export version",
        ),
        capabilities=tuple(cast(list[str], capabilities)),
        definitions=definitions,
    )


def _definition(value: object, position: int) -> DefinitionDescription:
    path = f"session definition {position}"
    data = _mapping(value, path)
    _exact(
        data,
        {
            "name",
            "cell_id",
            "python_type",
            "kind",
            "input_mode",
            "siblings",
            "portable_input",
            "sensitive",
            "value_available",
            "value",
            "domain",
        },
        path,
    )
    siblings = data["siblings"]
    kind = data["kind"]
    input_mode = data["input_mode"]
    if not isinstance(siblings, list) or any(not isinstance(item, str) for item in siblings):
        raise SessionError(f"{path} siblings are invalid")
    if kind not in {"ordinary", "ui"}:
        raise SessionError(f"{path} kind is invalid")
    if input_mode not in {"value", "patch"}:
        raise SessionError(f"{path} input_mode is invalid")
    domain = _mapping(data["domain"], f"{path} domain")
    return DefinitionDescription(
        name=_string(data["name"], f"{path} name"),
        cell_id=_string(data["cell_id"], f"{path} cell_id"),
        python_type=_string(data["python_type"], f"{path} python_type"),
        kind=cast(Literal["ordinary", "ui"], kind),
        input_mode=cast(Literal["value", "patch"], input_mode),
        siblings=tuple(cast(list[str], siblings)),
        portable_input=_boolean(data["portable_input"], f"{path} portable_input"),
        sensitive=_boolean(data["sensitive"], f"{path} sensitive"),
        value_available=_boolean(data["value_available"], f"{path} value_available"),
        value=cast(JsonValue, data["value"]),
        domain=domain,
    )


def _bridge_error(error: BridgeError) -> Exception:
    code = error.remote_code
    kwargs = {"code": code, "details": error.details}
    if code.startswith("spec_"):
        return SpecError(str(error), **kwargs)
    if code in {"marimo_incompatible"}:
        return CompatibilityError(str(error), **kwargs)
    if (
        code.startswith("output_")
        or code.startswith("cache_receipt")
        or code.startswith("exporter_")
    ):
        return OutputError(str(error), **kwargs)
    if code.startswith("codec_"):
        return CodecError(str(error), **kwargs)
    if code.startswith("state_") or code.startswith("input_") or code.startswith("parent_"):
        return ExecutionError(str(error), **kwargs)
    if code.startswith("integrity_"):
        return IntegrityError(str(error), **kwargs)
    return SessionError(str(error), **kwargs)


def _export_result(
    captured: _CaptureData,
    written: WriteResult,
    *,
    mode: Literal["build", "capture"],
    session_id: str | None,
    timings: PhaseTimings,
) -> ExportResult:
    index = captured.index
    return ExportResult(
        path=written.path,
        mode=mode,
        session_id=session_id,
        notebook_filename=index.notebook.filename,
        document_sha256=index.notebook.document_sha256,
        producer=index.producer,
        states=tuple(index.states),
        outputs=index.outputs,
        assets=written.assets,
        asset_bytes=written.asset_bytes,
        index_bytes=written.index_bytes,
        output_cache=captured.output_cache,
        notebook_cache=captured.notebook_cache,
        timings=timings,
        warnings=written.warnings,
    )


def _timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("timeout must be a number")
    result = float(value)
    if result <= 0 or not math.isfinite(result):
        raise ValueError("timeout must be a positive finite number")
    return result


def _mapping(value: object, path: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise TransportError(f"{path} must be an object")
    try:
        return json_object(value, path)
    except (TypeError, ValueError) as error:
        raise TransportError(f"{path} is invalid") from error


def _exact(value: Mapping[str, object], expected: set[str], path: str) -> None:
    if set(value) != expected:
        raise TransportError(f"{path} has invalid fields")


def _string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise SessionError(f"{path} must be a non-empty string")
    return value


def _optional_string(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _string(value, path)


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise SessionError(f"{path} must be a boolean")
    return value


def _digest(value: object, path: str) -> str:
    digest = _string(value, path)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise SessionError(f"{path} must be a lowercase SHA-256 digest")
    return digest


def _freeze(value: JsonValue) -> FrozenJsonValue:
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    return value


def _thaw(value: FrozenJsonValue) -> JsonValue:
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, Mapping):
        items = cast(Mapping[str, FrozenJsonValue], value)
        return {key: _thaw(item) for key, item in items.items()}
    return value


__all__ = [
    "Client",
    "DefinitionDescription",
    "Session",
    "SessionDescription",
    "capture",
]
