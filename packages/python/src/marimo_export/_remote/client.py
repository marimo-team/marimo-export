from __future__ import annotations

import json
import math
import secrets
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Protocol, TypeGuard, cast
from urllib.parse import SplitResult, unquote, urljoin, urlsplit

from marimo_export._diagnostics import safe_diagnostic
from marimo_export._identity import ImplementationDriftError, require_implementation_stable
from marimo_export._json import JsonObject, JsonValue, decode_json, json_object
from marimo_export.errors import TransportError

from .auth import auth_headers, parse_server_address
from .sse import SSEError, SSEEvent, SSEParser

BRIDGE_SCHEMA = "marimo-export.bridge.v1"

_CHUNK_BYTES = 64 * 1024
_DEFAULT_MAX_EVENT_BYTES = 40 * 1024 * 1024
_DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_SESSION_REGISTRY_BYTES = 1024 * 1024
_MAX_SESSION_METADATA_CHARS = 32 * 1024
_STDOUT_CHUNK_CHARS = 64 * 1024
_STDERR_HEAD_CHARS = 2048
_STDERR_TAIL_CHARS = 4096
_STDERR_DIAGNOSTIC_CHARS = 8192
_OPERATIONS = frozenset(
    {"validate_baseline", "inspect", "observe_inputs", "plan", "capture", "release"}
)


@dataclass(frozen=True)
class SessionInfo:
    id: str
    filename: str | None
    path: str | None


class BridgeError(TransportError):
    """A structured failure returned by the kernel bridge."""

    def __init__(
        self,
        remote_code: str,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        self.remote_code = remote_code
        super().__init__(message, details=details)


class KernelTransport(Protocol):
    """Outbound capabilities required by the public capture client."""

    def list_sessions(self) -> tuple[SessionInfo, ...]: ...

    def invoke(
        self,
        session_id: str,
        operation: str,
        params: Mapping[str, object],
    ) -> dict[str, object]: ...

    def download_asset(
        self,
        session_id: str,
        url: str,
        maximum_bytes: int,
    ) -> bytes: ...


class _Response(Protocol):
    @property
    def headers(self) -> Mapping[str, str]: ...

    def getcode(self) -> int: ...

    def read(self, size: int = -1) -> bytes: ...

    def close(self) -> None: ...


class _Opener(Protocol):
    def open(
        self,
        request: urllib.request.Request,
        data: bytes | None = None,
        timeout: float = ...,
    ) -> _Response: ...


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request | None:
        del req, fp, code, msg, headers, newurl
        return None


class HttpKernelTransport:
    """Attach to an active marimo session over its edit HTTP API."""

    def __init__(
        self,
        server: str,
        *,
        access_token: str | None = None,
        server_token: str | None = None,
        timeout: float = 300.0,
        maximum_event_bytes: int = _DEFAULT_MAX_EVENT_BYTES,
        maximum_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
        _opener: _Opener | None = None,
    ) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeout must be a positive number of seconds.")
        if timeout <= 0 or not math.isfinite(timeout):
            raise ValueError("timeout must be a positive number of seconds.")
        if (
            isinstance(maximum_event_bytes, bool)
            or not isinstance(maximum_event_bytes, int)
            or maximum_event_bytes <= 0
        ):
            raise ValueError("maximum_event_bytes must be a positive integer.")
        if (
            isinstance(maximum_response_bytes, bool)
            or not isinstance(maximum_response_bytes, int)
            or maximum_response_bytes <= 0
        ):
            raise ValueError("maximum_response_bytes must be a positive integer.")

        self._address = parse_server_address(
            server,
            access_token=access_token,
        )
        self._headers = auth_headers(
            self._address,
            server_token=server_token,
        )
        self._secrets = tuple(
            sorted(
                {
                    value
                    for value in (self._address.access_token, server_token)
                    if value is not None
                },
                key=len,
                reverse=True,
            )
        )
        self._timeout = float(timeout)
        self._maximum_event_bytes = maximum_event_bytes
        self._maximum_response_bytes = maximum_response_bytes
        self._opener = _opener or cast(
            _Opener,
            urllib.request.build_opener(_RejectRedirects()),
        )

    @property
    def server(self) -> str:
        """The normalized server URL with credentials removed."""

        return self._address.base_url

    @property
    def _credential_values(self) -> tuple[str, ...]:
        return self._secrets

    def list_sessions(self) -> tuple[SessionInfo, ...]:
        request = self._request("api/sessions")
        response = self._open(request, "session discovery")
        try:
            body = self._read_bounded(
                response,
                _MAX_SESSION_REGISTRY_BYTES,
                "marimo session registry",
            )
        finally:
            response.close()
        try:
            value = _loads_json(body.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, ValueError) as error:
            raise TransportError("marimo returned an invalid session registry.") from error
        if not isinstance(value, dict):
            raise TransportError("marimo returned an invalid session registry.")

        sessions: list[SessionInfo] = []
        for session_id, raw in value.items():
            if not isinstance(session_id, str):
                raise TransportError("marimo returned an invalid session registry.")
            try:
                _validate_session_id(session_id)
            except ValueError:
                raise TransportError("marimo returned an invalid session registry.") from None
            if not isinstance(raw, dict):
                raise TransportError("marimo returned an invalid session registry.")
            if "filename" not in raw or "path" not in raw:
                raise TransportError("marimo returned an invalid session registry.")
            filename = raw.get("filename")
            path = raw.get("path")
            if not _nullable_string(filename) or not _nullable_string(path):
                raise TransportError("marimo returned an invalid session registry.")
            sessions.append(
                SessionInfo(
                    id=session_id,
                    filename=filename,
                    path=path,
                )
            )
        return tuple(sorted(sessions, key=lambda session: session.id))

    def invoke(
        self,
        session_id: str,
        operation: str,
        params: Mapping[str, object],
    ) -> dict[str, object]:
        _validate_session_id(session_id)
        if operation not in _OPERATIONS:
            raise ValueError(
                "operation must be validate_baseline, inspect, observe_inputs, plan, "
                "capture, or release."
            )
        if not isinstance(params, Mapping):
            raise TypeError("params must be a mapping.")

        request_id = secrets.token_hex(16)
        marker = f"__MARIMO_EXPORT_{secrets.token_hex(24)}__:"
        client_identity = _require_client_implementation()
        request_value = {
            "schema": BRIDGE_SCHEMA,
            "client_version": _package_version(),
            "client_identity": client_identity,
            "request_id": request_id,
            "operation": operation,
            "params": dict(params),
        }
        try:
            request_json = json.dumps(
                request_value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as error:
            raise TypeError("bridge params must contain JSON values.") from error
        code = "\n".join(
            (
                f"_marimo_export_request_json = {request_json!r}",
                "import marimo_export._marimo.bridge as _marimo_export_bridge",
                "_marimo_export_response_json = await "
                "_marimo_export_bridge.dispatch_json(_marimo_export_request_json)",
                f"_marimo_export_output = {marker!r} + _marimo_export_response_json + '\\n'",
                f"_marimo_export_chunk_chars = {_STDOUT_CHUNK_CHARS}",
                "for _marimo_export_offset in range("
                "0, len(_marimo_export_output), _marimo_export_chunk_chars):",
                "    print(_marimo_export_output["
                "_marimo_export_offset:_marimo_export_offset + _marimo_export_chunk_chars], "
                "end='', flush=True)",
            )
        )
        body = json.dumps(
            {"code": code},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = self._request(
            "api/kernel/execute",
            method="POST",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Marimo-Session-Id": session_id,
            },
        )
        response = self._open(request, f"remote {operation}")
        try:
            envelope = self._read_execution(response, marker)
        finally:
            response.close()
        data = self._parse_envelope(envelope, request_id)
        _require_client_implementation()
        return data

    def download_asset(
        self,
        session_id: str,
        url: str,
        maximum_bytes: int,
    ) -> bytes:
        _validate_session_id(session_id)
        if (
            isinstance(maximum_bytes, bool)
            or not isinstance(maximum_bytes, int)
            or maximum_bytes <= 0
        ):
            raise ValueError("maximum_bytes must be a positive integer.")
        resolved = self._asset_url(url)
        request = self._absolute_request(
            resolved,
            headers={"Marimo-Session-Id": session_id},
        )
        response = self._open(request, "asset download")
        try:
            return self._read_bounded(
                response,
                maximum_bytes,
                "marimo cache asset",
            )
        finally:
            response.close()

    def _read_execution(self, response: _Response, marker: str) -> str:
        parser = SSEParser(self._maximum_event_bytes)
        extractor = _MarkerExtractor(marker, self._maximum_response_bytes)
        stderr = _StderrDiagnostic(self._secrets)
        completed = False
        success = False
        deadline = time.monotonic() + self._timeout
        try:
            while not completed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TransportError(
                        "marimo scratchpad execution timed out. Remote work may still be running."
                    )
                _set_response_read_timeout(response, remaining)
                chunk = _read_response_chunk(response, _CHUNK_BYTES)
                if time.monotonic() >= deadline:
                    raise TransportError(
                        "marimo scratchpad execution timed out. Remote work may still be running."
                    )
                if not chunk:
                    break
                deadline = time.monotonic() + self._timeout
                for event in parser.feed(chunk):
                    completed, success = _dispatch_event(
                        event,
                        extractor,
                        stderr,
                        completed,
                    )
                    if completed:
                        break
            if not completed:
                for event in parser.close():
                    completed, success = _dispatch_event(
                        event,
                        extractor,
                        stderr,
                        completed,
                    )
            if not completed:
                raise TransportError("marimo scratchpad stream ended before its done event.")
            if not success:
                diagnostic = stderr.finish()
                raise TransportError(
                    "marimo scratchpad execution failed.",
                    details={"stderr": diagnostic} if diagnostic else None,
                )
            return extractor.finish()
        except SSEError as error:
            raise TransportError(str(error)) from error
        except TimeoutError as error:
            raise TransportError(
                "marimo scratchpad execution timed out. Remote work may still be running."
            ) from error
        except OSError as error:
            raise TransportError(
                "marimo scratchpad execution stream failed. Remote work may still be running."
            ) from error

    def _parse_envelope(
        self,
        response_json: str,
        request_id: str,
    ) -> dict[str, object]:
        try:
            value = _loads_json(response_json)
        except ValueError as error:
            raise TransportError("The kernel bridge returned malformed response JSON.") from error
        if not isinstance(value, dict):
            raise TransportError("The kernel bridge returned an invalid response envelope.")
        if value.get("schema") != BRIDGE_SCHEMA:
            raise TransportError("The kernel bridge response schema does not match the client.")
        if value.get("request_id") != request_id:
            raise TransportError("The kernel bridge response does not match the request.")
        ok = value.get("ok")
        if ok is True:
            if set(value) != {"schema", "request_id", "ok", "data"}:
                raise TransportError("The kernel bridge returned an invalid success envelope.")
            data = value.get("data")
            if not isinstance(data, dict):
                raise TransportError("The kernel bridge returned invalid response data.")
            return cast(dict[str, object], data)
        if ok is False:
            if set(value) != {"schema", "request_id", "ok", "error"}:
                raise TransportError("The kernel bridge returned an invalid error envelope.")
            error = value.get("error")
            if not isinstance(error, dict) or not {"code", "message"} <= set(error):
                raise TransportError("The kernel bridge returned invalid error data.")
            if set(error) - {"code", "message", "details"}:
                raise TransportError("The kernel bridge returned invalid error data.")
            code = error.get("code")
            message = error.get("message")
            if not isinstance(code, str) or not code:
                raise TransportError("The kernel bridge returned invalid error data.")
            if not isinstance(message, str) or not message:
                raise TransportError("The kernel bridge returned invalid error data.")
            raw_details = error.get("details", {})
            if not isinstance(raw_details, Mapping):
                raise TransportError("The kernel bridge returned invalid error data.")
            try:
                details = json_object(raw_details, "bridge error.details")
            except (TypeError, ValueError) as details_error:
                raise TransportError(
                    "The kernel bridge returned invalid error data."
                ) from details_error
            raise BridgeError(
                code,
                self._redact(message),
                details=self._redact_details(details),
            )
        raise TransportError("The kernel bridge returned an invalid response envelope.")

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> urllib.request.Request:
        url = urljoin(self._address.base_url, path)
        return self._absolute_request(
            url,
            method=method,
            data=data,
            headers=headers,
        )

    def _absolute_request(
        self,
        url: str,
        *,
        method: str = "GET",
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> urllib.request.Request:
        request_headers = dict(self._headers)
        if headers is not None:
            request_headers.update(headers)
        return urllib.request.Request(
            url,
            data=data,
            headers=request_headers,
            method=method,
        )

    def _open(self, request: urllib.request.Request, operation: str) -> _Response:
        try:
            response = self._opener.open(request, timeout=self._timeout)
            status = response.getcode()
            if not 200 <= status < 300:
                response.close()
                raise TransportError(f"marimo {operation} failed with HTTP status {status}.")
            return response
        except TransportError:
            raise
        except urllib.error.HTTPError as error:
            raise TransportError(
                f"marimo {operation} failed with HTTP status {error.code}."
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise TransportError(
                f"Could not complete marimo {operation} at {self.server}."
            ) from None

    def _read_bounded(
        self,
        response: _Response,
        maximum_bytes: int,
        label: str,
    ) -> bytes:
        content_length = _content_length(response.headers)
        if content_length is not None and content_length > maximum_bytes:
            raise TransportError(f"The {label} exceeds the {maximum_bytes}-byte transport limit.")
        chunks: list[bytes] = []
        size = 0
        deadline = time.monotonic() + self._timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TransportError(f"The {label} download timed out.")
                _set_response_read_timeout(response, remaining)
                chunk = _read_response_chunk(
                    response,
                    min(_CHUNK_BYTES, maximum_bytes - size + 1),
                )
                if time.monotonic() >= deadline:
                    raise TransportError(f"The {label} download timed out.")
                if not chunk:
                    break
                size += len(chunk)
                if size > maximum_bytes:
                    raise TransportError(
                        f"The {label} exceeds the {maximum_bytes}-byte transport limit."
                    )
                chunks.append(chunk)
        except TransportError:
            raise
        except TimeoutError:
            raise TransportError(f"The {label} download timed out.") from None
        except OSError:
            raise TransportError(f"The {label} stream failed.") from None
        return b"".join(chunks)

    def _asset_url(self, value: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("asset URL must be a non-empty string.")
        try:
            parsed_value = urlsplit(value)
        except ValueError as error:
            raise TransportError("The bridge returned an invalid asset URL.") from error
        if parsed_value.username is not None or parsed_value.password is not None:
            raise TransportError("The bridge returned an unsafe asset URL.")
        if parsed_value.fragment or parsed_value.query:
            raise TransportError("The bridge returned an unsafe asset URL.")
        resolved = urljoin(self._address.base_url, value)
        base = urlsplit(self._address.base_url)
        target = urlsplit(resolved)
        try:
            same_origin = _origin(base) == _origin(target)
        except ValueError as error:
            raise TransportError("The bridge returned an invalid asset URL.") from error
        if not same_origin:
            raise TransportError("The bridge asset URL must use the marimo server origin.")
        base_path = base.path if base.path.endswith("/") else f"{base.path}/"
        prefix = f"{base_path}@file/"
        if not target.path.startswith(prefix):
            raise TransportError("The bridge asset URL must identify a marimo virtual file.")
        filename = unquote(target.path[len(prefix) :])
        if (
            not filename
            or filename in {".", ".."}
            or "/" in filename
            or "\\" in filename
            or any(ord(character) < 32 for character in filename)
        ):
            raise TransportError("The bridge returned an unsafe asset URL.")
        return resolved

    def _redact(self, message: str) -> str:
        return safe_diagnostic(
            message,
            secrets=self._secrets,
            maximum_chars=4096,
        )

    def _redact_details(self, details: JsonObject) -> JsonObject:
        def redact(value: JsonValue) -> JsonValue:
            if isinstance(value, str):
                return self._redact(value)
            if isinstance(value, list):
                return [redact(item) for item in value]
            if isinstance(value, dict):
                return redact_object(value)
            return value

        def redact_object(value: JsonObject) -> JsonObject:
            result: JsonObject = {}
            for key, item in value.items():
                base = self._redact(key)
                redacted_key = base
                suffix = 2
                while redacted_key in result:
                    redacted_key = f"{base}#{suffix}"
                    suffix += 1
                result[redacted_key] = redact(item)
            return result

        return redact_object(details)


class _MarkerExtractor:
    def __init__(self, marker: str, maximum_bytes: int) -> None:
        self._marker = marker
        self._maximum = maximum_bytes
        self._buffer = ""
        self._buffer_bytes = 0
        self._capturing = False
        self._response: str | None = None

    def feed(self, text: str) -> None:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if self._response is not None:
            if self._marker in text:
                raise TransportError("The kernel bridge returned multiple correlated responses.")
            return
        if not self._capturing:
            self._buffer += text
            index = self._buffer.find(self._marker)
            if index < 0:
                keep = max(0, len(self._marker) - 1)
                self._buffer = self._buffer[-keep:] if keep else ""
                return
            self._buffer = self._buffer[index + len(self._marker) :]
            self._buffer_bytes = len(self._buffer.encode("utf-8"))
            self._capturing = True
        else:
            self._buffer += text
            self._buffer_bytes += len(text.encode("utf-8"))

        newline = self._buffer.find("\n")
        if newline >= 0:
            candidate = self._buffer[:newline]
            remainder = self._buffer[newline + 1 :]
            self._check_size(len(candidate.encode("utf-8")))
            if not candidate:
                raise TransportError("The kernel bridge returned an empty response.")
            self._response = candidate
            self._buffer = ""
            self._buffer_bytes = 0
            if self._marker in remainder:
                raise TransportError("The kernel bridge returned multiple correlated responses.")
            return
        self._check_size(self._buffer_bytes)

    def finish(self) -> str:
        if self._response is not None:
            return self._response
        if self._capturing and self._buffer:
            self._check_size(self._buffer_bytes)
            return self._buffer
        raise TransportError(
            "The kernel bridge response marker was missing from scratchpad output."
        )

    def _check_size(self, size: int) -> None:
        if size > self._maximum:
            raise TransportError("The kernel bridge response exceeded the transport limit.")


class _StderrDiagnostic:
    def __init__(self, secrets: tuple[str, ...]) -> None:
        self._secrets = secrets
        self._head = ""
        self._tail = ""
        self._length = 0

    def feed(self, value: str) -> None:
        self._length += len(value)
        if len(self._head) < _STDERR_HEAD_CHARS:
            remaining = _STDERR_HEAD_CHARS - len(self._head)
            self._head += value[:remaining]
        if len(value) >= _STDERR_TAIL_CHARS:
            self._tail = value[-_STDERR_TAIL_CHARS:]
        else:
            self._tail = (self._tail + value)[-_STDERR_TAIL_CHARS:]

    def finish(self) -> str:
        if self._length == 0:
            return ""
        if self._length <= len(self._tail):
            return safe_diagnostic(
                self._tail,
                secrets=self._secrets,
                maximum_chars=_STDERR_DIAGNOSTIC_CHARS,
            )
        tail_start = self._length - len(self._tail)
        overlap = max(0, len(self._head) - tail_start)
        tail = self._tail[overlap:]
        covered = len(self._head) + len(tail)
        marker = "\n... stderr truncated ...\n" if covered < self._length else ""
        head = safe_diagnostic(
            self._head,
            secrets=self._secrets,
            maximum_chars=3072,
        )
        rendered_tail = safe_diagnostic(
            tail,
            secrets=self._secrets,
            maximum_chars=4864,
        )
        return f"{head}{marker}{rendered_tail}"


def _dispatch_event(
    event: SSEEvent,
    extractor: _MarkerExtractor,
    stderr: _StderrDiagnostic,
    completed: bool,
) -> tuple[bool, bool]:
    if completed:
        raise TransportError("marimo returned events after the scratchpad done event.")
    try:
        payload = _loads_json(event.data)
    except ValueError as error:
        raise TransportError("marimo returned invalid scratchpad event JSON.") from error
    if not isinstance(payload, dict):
        raise TransportError("marimo returned an invalid scratchpad event.")
    if event.event in {"stdout", "stderr"}:
        value = payload.get("data")
        if not isinstance(value, str):
            raise TransportError("marimo returned invalid scratchpad output.")
        if event.event == "stdout":
            extractor.feed(value)
        else:
            stderr.feed(value)
        return False, False
    if event.event != "done":
        return False, False
    success = payload.get("success")
    if type(success) is not bool:
        raise TransportError("marimo returned an invalid scratchpad done event.")
    return True, success


def _loads_json(value: str) -> object:
    return decode_json(value, "remote JSON")


def _validate_session_id(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1024
        or any(
            ord(character) < 32 or ord(character) == 127 or ord(character) > 255
            for character in value
        )
    ):
        raise ValueError("session_id must be a non-empty marimo session ID.")


def _nullable_string(value: object) -> TypeGuard[str | None]:
    return value is None or (
        isinstance(value, str)
        and bool(value)
        and len(value) <= _MAX_SESSION_METADATA_CHARS
        and "\x00" not in value
    )


def _content_length(headers: object) -> int | None:
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    value = getter("Content-Length")
    if value is None:
        return None
    if not isinstance(value, str):
        raise TransportError("The server returned an invalid Content-Length.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise TransportError("The server returned an invalid Content-Length.") from error
    if parsed < 0:
        raise TransportError("The server returned an invalid Content-Length.")
    return parsed


def _read_response_chunk(response: _Response, size: int) -> bytes:
    read1 = getattr(response, "read1", None)
    if callable(read1):
        return cast(bytes, read1(size))
    return response.read(size)


def _set_response_read_timeout(response: _Response, seconds: float) -> None:
    # urllib exposes no public per-read timeout setter. Its HTTPResponse owns
    # the socket through this stable IO chain, which keeps the overall request
    # deadline authoritative after earlier chunks consume part of the budget.
    stream = getattr(response, "fp", None)
    raw = getattr(stream, "raw", None)
    connection = getattr(raw, "_sock", None)
    settimeout = getattr(connection, "settimeout", None)
    if callable(settimeout):
        settimeout(max(seconds, 0.001))


def _origin(value: SplitResult) -> tuple[str, str, int | None]:
    scheme = value.scheme.lower()
    hostname = value.hostname
    if hostname is None:
        raise TransportError("The bridge returned an invalid asset URL.")
    port = value.port
    default_port = 80 if scheme == "http" else 443
    return scheme, hostname.lower(), port if port is not None else default_port


def _package_version() -> str:
    try:
        return version("marimo-export")
    except PackageNotFoundError:
        return "0+unknown"


def _require_client_implementation() -> str:
    try:
        return require_implementation_stable()
    except ImplementationDriftError as error:
        raise TransportError(
            str(error),
            code="implementation_changed",
            details={"loaded": error.loaded, "current": error.current},
        ) from error
