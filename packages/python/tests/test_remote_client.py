from __future__ import annotations

import ast
import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from importlib.metadata import version
from typing import Any, cast

import marimo_export._marimo.bridge as bridge_module
import marimo_export._remote.client as remote_client
import pytest
from marimo_export._execution import Baseline
from marimo_export._identity import ImplementationDriftError, implementation_identity
from marimo_export._marimo.bridge import dispatch_json
from marimo_export._marimo.capabilities import MarimoCapabilities
from marimo_export._remote.client import (
    BRIDGE_SCHEMA,
    BridgeError,
    HttpKernelTransport,
    SessionInfo,
)
from marimo_export.errors import SessionError, TransportError


class Response:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self._body = io.BytesIO(body)
        self._chunks = iter(chunks) if chunks is not None else None
        self._status = status
        self._headers = headers or {}
        self.closed = False

    @property
    def headers(self) -> dict[str, str]:
        return self._headers

    def getcode(self) -> int:
        return self._status

    def read(self, size: int = -1) -> bytes:
        if self._chunks is not None:
            return next(self._chunks, b"")
        return self._body.read(size)

    def close(self) -> None:
        self.closed = True


class Opener:
    def __init__(
        self,
        respond: Callable[[urllib.request.Request], Response],
    ) -> None:
        self.respond = respond
        self.requests: list[urllib.request.Request] = []
        self.timeouts: list[float] = []

    def open(
        self,
        request: urllib.request.Request,
        data: bytes | None = None,
        timeout: float = 0,
    ) -> Response:
        assert data is None
        self.requests.append(request)
        self.timeouts.append(timeout)
        return self.respond(request)


def test_session_discovery_is_validated_and_deterministic() -> None:
    opener = Opener(
        lambda _request: Response(
            json.dumps(
                {
                    "session-b": {"filename": "b.py", "path": "/srv/b.py"},
                    "session-a": {"filename": None, "path": None},
                }
            ).encode()
        )
    )
    transport = HttpKernelTransport(
        "https://marimo.test/root/",
        access_token="secret",
        _opener=opener,
    )

    assert transport.server == "https://marimo.test/root/"
    assert transport.list_sessions() == (
        SessionInfo(id="session-a", filename=None, path=None),
        SessionInfo(id="session-b", filename="b.py", path="/srv/b.py"),
    )
    request = opener.requests[0]
    assert request.full_url == "https://marimo.test/root/api/sessions"
    assert request.get_header("Authorization") == "Bearer secret"


def test_invoke_posts_correlated_bridge_request_once() -> None:
    captured: dict[str, Any] = {}

    def respond(request: urllib.request.Request) -> Response:
        bridge_request, marker = decode_execute_request(request)
        captured.update(bridge_request)
        envelope = {
            "schema": BRIDGE_SCHEMA,
            "request_id": bridge_request["request_id"],
            "ok": True,
            "data": {"ticket": "ticket-1"},
        }
        return scratchpad_response(marker, envelope, crlf=True)

    opener = Opener(respond)
    transport = HttpKernelTransport(
        "https://marimo.test/",
        access_token="auth-secret",
        server_token="server-secret",
        _opener=opener,
    )

    assert transport.invoke("session-1", "capture", {"spec": {}}) == {"ticket": "ticket-1"}
    assert captured == {
        "schema": BRIDGE_SCHEMA,
        "client_version": captured["client_version"],
        "client_identity": captured["client_identity"],
        "request_id": captured["request_id"],
        "operation": "capture",
        "params": {"spec": {}},
    }
    assert isinstance(captured["client_version"], str)
    assert captured["client_version"]
    assert isinstance(captured["client_identity"], str)
    assert len(captured["client_identity"]) == 64
    assert len(opener.requests) == 1
    request = opener.requests[0]
    headers = {name.lower(): value for name, value in request.header_items()}
    assert request.method == "POST"
    assert request.full_url == "https://marimo.test/api/kernel/execute"
    assert headers["authorization"] == "Bearer auth-secret"
    assert headers["marimo-server-token"] == "server-secret"
    assert headers["marimo-session-id"] == "session-1"


def test_transport_rejects_operation_outside_the_bridge_contract() -> None:
    transport = HttpKernelTransport(
        "https://marimo.test/",
        _opener=Opener(lambda _request: Response(b"")),
    )

    with pytest.raises(
        ValueError,
        match="validate_baseline, inspect, observe_inputs, plan, capture, or release",
    ):
        transport.invoke("session-1", "unknown", {})


@pytest.mark.asyncio
async def test_bridge_rejects_operation_outside_the_contract() -> None:
    response = json.loads(
        await dispatch_json(
            json.dumps(
                {
                    "schema": BRIDGE_SCHEMA,
                    "client_version": version("marimo-export"),
                    "client_identity": implementation_identity(),
                    "request_id": "request-invalid-operation",
                    "operation": "unknown",
                    "params": {},
                }
            )
        )
    )

    assert response["ok"] is False
    assert response["error"] == {
        "code": "session_error",
        "message": (
            "bridge operation must be validate_baseline, inspect, observe_inputs, "
            "plan, capture, or release"
        ),
    }


def test_invoke_rejects_loaded_source_drift_before_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = Opener(lambda _request: (_ for _ in ()).throw(AssertionError("request sent")))
    transport = HttpKernelTransport("https://marimo.test/", _opener=opener)

    def drift() -> str:
        raise ImplementationDriftError("a" * 64, "b" * 64)

    monkeypatch.setattr(remote_client, "require_implementation_stable", drift)

    with pytest.raises(TransportError, match="restart") as raised:
        transport.invoke("session-1", "inspect", {})

    assert raised.value.code == "implementation_changed"
    assert opener.requests == []


def test_invoke_rejects_source_drift_after_bridge_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def respond(request: urllib.request.Request) -> Response:
        bridge_request, marker = decode_execute_request(request)
        return scratchpad_response(
            marker,
            {
                "schema": BRIDGE_SCHEMA,
                "request_id": bridge_request["request_id"],
                "ok": True,
                "data": {"filename": "notebook.py"},
            },
        )

    opener = Opener(respond)
    transport = HttpKernelTransport("https://marimo.test/", _opener=opener)
    calls = 0

    def stable_then_drift() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            return "a" * 64
        raise ImplementationDriftError("a" * 64, "b" * 64)

    monkeypatch.setattr(remote_client, "require_implementation_stable", stable_then_drift)

    with pytest.raises(TransportError, match="restart") as raised:
        transport.invoke("session-1", "inspect", {})

    assert raised.value.code == "implementation_changed"
    assert len(opener.requests) == 1


@pytest.mark.asyncio
async def test_bridge_rejects_a_stale_development_implementation() -> None:
    response = json.loads(
        await dispatch_json(
            json.dumps(
                {
                    "schema": BRIDGE_SCHEMA,
                    "client_version": version("marimo-export"),
                    "client_identity": "0" * 64,
                    "request_id": "request-1",
                    "operation": "inspect",
                    "params": {},
                }
            )
        )
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "bridge_version_mismatch"


@pytest.mark.asyncio
async def test_inspection_rejects_source_drift_before_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Runtime:
        @staticmethod
        def require_capabilities() -> MarimoCapabilities:
            return MarimoCapabilities("test", ())

        @staticmethod
        async def inspect_baseline() -> Baseline:
            return Baseline(
                definitions={},
                cells=(),
                document_sha256="d" * 64,
                filename="notebook.py",
            )

        @staticmethod
        def runtime_path() -> str:
            return "/notebook.py"

    calls = 0

    def stable_then_drift() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            return "a" * 64
        raise ImplementationDriftError("a" * 64, "b" * 64)

    monkeypatch.setattr(bridge_module, "require_implementation_stable", stable_then_drift)

    with pytest.raises(SessionError) as raised:
        await bridge_module._inspect(cast(Any, Runtime()))

    assert raised.value.code == "implementation_changed"


def test_invoke_reads_a_bridge_response_larger_than_one_stdout_write() -> None:
    value = "x" * 1_100_000

    def respond(request: urllib.request.Request) -> Response:
        bridge_request, marker = decode_execute_request(request)
        envelope = {
            "schema": BRIDGE_SCHEMA,
            "request_id": bridge_request["request_id"],
            "ok": True,
            "data": {"value": value},
        }
        return chunked_scratchpad_response(marker, envelope)

    transport = HttpKernelTransport(
        "https://marimo.test/",
        _opener=Opener(respond),
    )

    assert transport.invoke("session-1", "inspect", {}) == {"value": value}


def test_invoke_surfaces_structured_bridge_error_and_redacts_tokens() -> None:
    def respond(request: urllib.request.Request) -> Response:
        bridge_request, marker = decode_execute_request(request)
        envelope = {
            "schema": BRIDGE_SCHEMA,
            "request_id": bridge_request["request_id"],
            "ok": False,
            "error": {
                "code": "selection_error",
                "message": "token auth-secret could not select value",
                "details": {
                    "source": "auth-secret",
                    "auth-secret": "first",
                    "<redacted>": "second",
                    "nested": [
                        "safe",
                        "auth-secret",
                        {"field-auth-secret": "auth-secret"},
                    ],
                },
            },
        }
        return scratchpad_response(marker, envelope)

    transport = HttpKernelTransport(
        "https://marimo.test/",
        access_token="auth",
        server_token="auth-secret",
        _opener=Opener(respond),
    )

    with pytest.raises(BridgeError) as caught:
        transport.invoke("session-1", "inspect", {})
    assert caught.value.remote_code == "selection_error"
    assert str(caught.value) == "token <redacted> could not select value"
    assert caught.value.details == {
        "source": "<redacted>",
        "<redacted>": "first",
        "<redacted>#2": "second",
        "nested": [
            "safe",
            "<redacted>",
            {"field-<redacted>": "<redacted>"},
        ],
    }


def test_failed_scratchpad_preserves_bounded_redacted_stderr() -> None:
    stderr = "start auth-secret\n" + "x" * 10_000 + "\nend auth-secret"

    def respond(request: urllib.request.Request) -> Response:
        decode_execute_request(request)
        output = json.dumps({"data": stderr})
        done = json.dumps(
            {
                "success": False,
                "output": {"mimetype": "text/plain", "data": ""},
            }
        )
        body = f"event: stderr\ndata: {output}\n\nevent: done\ndata: {done}\n\n"
        return Response(body.encode())

    transport = HttpKernelTransport(
        "https://marimo.test/",
        access_token="auth-secret",
        _opener=Opener(respond),
    )

    with pytest.raises(TransportError, match="scratchpad execution failed") as caught:
        transport.invoke("session-1", "inspect", {})

    diagnostic = cast(str, caught.value.details["stderr"])
    assert "auth-secret" not in diagnostic
    assert "start <redacted>" in diagnostic
    assert "end <redacted>" in diagnostic
    assert "stderr truncated" in diagnostic
    assert len(diagnostic) <= 8192


def test_invoke_rejects_mismatched_response_correlation() -> None:
    def respond(request: urllib.request.Request) -> Response:
        _bridge_request, marker = decode_execute_request(request)
        envelope = {
            "schema": BRIDGE_SCHEMA,
            "request_id": "another-request",
            "ok": True,
            "data": {},
        }
        return scratchpad_response(marker, envelope)

    transport = HttpKernelTransport(
        "https://marimo.test/",
        _opener=Opener(respond),
    )

    with pytest.raises(TransportError, match="does not match"):
        transport.invoke("session-1", "inspect", {})


def test_execute_request_is_never_retried_after_transport_failure() -> None:
    attempts = 0

    def fail(_request: urllib.request.Request) -> Response:
        nonlocal attempts
        attempts += 1
        raise urllib.error.URLError("offline")

    transport = HttpKernelTransport(
        "https://marimo.test/",
        _opener=Opener(fail),
    )

    with pytest.raises(TransportError, match="remote capture"):
        transport.invoke("session-1", "capture", {})
    assert attempts == 1


def test_asset_download_is_same_origin_virtual_file_and_strictly_bounded() -> None:
    opener = Opener(lambda _request: Response(b"asset-bytes"))
    transport = HttpKernelTransport(
        "https://marimo.test/root/",
        _opener=opener,
    )

    assert (
        transport.download_asset(
            "session-1",
            "./@file/11-planned_output.bin",
            11,
        )
        == b"asset-bytes"
    )
    assert opener.requests[0].full_url == ("https://marimo.test/root/@file/11-planned_output.bin")

    with pytest.raises(TransportError, match="server origin"):
        transport.download_asset(
            "session-1",
            "https://other.test/@file/11-planned_output.bin",
            11,
        )
    with pytest.raises(TransportError, match="unsafe asset URL"):
        transport.download_asset(
            "session-1",
            "./@file/%2e%2e/admin",
            11,
        )
    with pytest.raises(TransportError, match="transport limit"):
        transport.download_asset(
            "session-1",
            "./@file/11-planned_output.bin",
            10,
        )


@pytest.mark.parametrize(
    "body",
    [
        b'{"session":NaN}',
        b'{"session":{"filename":null,"path":null},"session":{}}',
    ],
)
def test_session_registry_rejects_noncanonical_json(body: bytes) -> None:
    transport = HttpKernelTransport(
        "https://marimo.test/",
        _opener=Opener(lambda _request: Response(body)),
    )

    with pytest.raises(TransportError, match="invalid session registry"):
        transport.list_sessions()


def decode_execute_request(
    request: urllib.request.Request,
) -> tuple[dict[str, Any], str]:
    assert request.data is not None
    body = json.loads(cast(bytes, request.data))
    tree = ast.parse(body["code"])
    request_assignment = tree.body[0]
    output_assignment = tree.body[3]
    assert isinstance(request_assignment, ast.Assign)
    assert isinstance(request_assignment.value, ast.Constant)
    assert isinstance(request_assignment.value.value, str)
    assert isinstance(output_assignment, ast.Assign)
    assert isinstance(output_assignment.value, ast.BinOp)
    marker_expression = output_assignment.value.left
    assert isinstance(marker_expression, ast.BinOp)
    assert isinstance(marker_expression.left, ast.Constant)
    assert isinstance(marker_expression.left.value, str)
    return json.loads(request_assignment.value.value), marker_expression.left.value


def scratchpad_response(
    marker: str,
    envelope: dict[str, Any],
    *,
    crlf: bool = False,
) -> Response:
    stdout = json.dumps({"data": f"unrelated output\n{marker}{json.dumps(envelope)}\n"})
    done = json.dumps(
        {
            "success": True,
            "output": {"mimetype": "text/plain", "data": ""},
        }
    )
    body = f"event: stdout\ndata: {stdout}\n\nevent: done\ndata: {done}\n\n"
    if crlf:
        encoded = body.replace("\n", "\r\n").encode()
        split = encoded.index(b"\r\n") + 1
        return Response(b"", chunks=[encoded[:split], encoded[split:]])
    return Response(body.encode())


def chunked_scratchpad_response(
    marker: str,
    envelope: dict[str, Any],
) -> Response:
    output = f"{marker}{json.dumps(envelope)}\n"
    events = []
    for offset in range(0, len(output), 64 * 1024):
        chunk = output[offset : offset + 64 * 1024]
        events.append(f"event: stdout\ndata: {json.dumps({'data': chunk})}\n\n")
    done = json.dumps(
        {
            "success": True,
            "output": {"mimetype": "text/plain", "data": ""},
        }
    )
    events.append(f"event: done\ndata: {done}\n\n")
    return Response("".join(events).encode())
