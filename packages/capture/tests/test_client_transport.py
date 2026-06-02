from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

import moexport.client._runtime as runtime_impl
import moexport.client._scratchpad as scratchpad_impl
import moexport.client._session as session_impl
from moexport.client._http import post_json
from moexport.client._scratchpad import can_import, execute_scratchpad, parse_sse
from moexport.client._session import notebook_matches, resolve_session


def _install_mock_transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    original_client = httpx.Client
    transport = httpx.MockTransport(handler)

    def client_factory(*args: Any, **kwargs: Any) -> httpx.Client:
        return original_client(*args, **kwargs, transport=transport)

    monkeypatch.setattr(httpx, "Client", client_factory)


def test_parse_sse_decodes_scratchpad_events() -> None:
    events = parse_sse(
        'event: stdout\ndata: {"data": "hello"}\n\n'
        'event: done\ndata: {"success": true, "output": null}\n\n'
    )

    assert events == [
        {"event": "stdout", "data": {"data": "hello"}},
        {"event": "done", "data": {"success": True, "output": None}},
    ]


def test_notebook_matches_path_name_or_suffix() -> None:
    record = {"path": "/tmp/project/notebook.py", "name": "notebook.py"}

    assert notebook_matches(record, "/tmp/project/notebook.py")
    assert notebook_matches(record, "notebook.py")
    assert not notebook_matches(record, "other.py")


def test_resolve_session_opens_notebook_when_no_running_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class Socket:
        def __enter__(self) -> Socket:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def recv(self, *, timeout: float) -> str:
            calls.append({"websocket_timeout": timeout})
            return json.dumps({"op": "kernel-ready"})

    def connect(url: str, **kwargs: Any) -> Socket:
        calls.append({"websocket_url": url, "kwargs": kwargs})
        return Socket()

    def post_json_stub(*args: Any, **kwargs: Any) -> dict[str, Any]:
        path = args[1]
        calls.append(
            {"path": path, "body": kwargs["body"], "headers": kwargs["headers"]}
        )
        if path == "/api/home/running_notebooks":
            return {"files": []}
        return {}

    monkeypatch.setattr(session_impl, "connect", connect)
    monkeypatch.setattr(session_impl, "post_json", post_json_stub)
    monkeypatch.setattr(session_impl, "random_session_id", lambda: "s_demo")

    session = resolve_session(
        server="https://marimo.example.test/base",
        notebook="/work/report.py",
        session_id=None,
        token="secret",
    )

    assert session.session_id == "s_demo"
    assert session.path == "/work/report.py"
    assert calls[1]["websocket_url"] == (
        "wss://marimo.example.test/base/ws?"
        "session_id=s_demo&file=%2Fwork%2Freport.py&access_token=secret"
    )
    assert calls[-1] == {
        "path": "/api/kernel/instantiate",
        "body": {"objectIds": [], "values": [], "autoRun": True},
        "headers": {"Marimo-Session-Id": "s_demo"},
    }


def test_post_json_sends_headers_and_decodes_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["headers"] = request.headers
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    _install_mock_transport(monkeypatch, handler)

    result = post_json(
        "http://localhost:2718/",
        "/api/demo",
        body={"answer": 42},
        headers={"Marimo-Session-Id": "session-1"},
        token="secret",
        timeout=5,
    )

    assert result == {"ok": True}
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/demo"
    assert captured["json"] == {"answer": 42}
    headers = captured["headers"]
    assert headers["accept"] == "application/json"
    assert headers["content-type"] == "application/json"
    assert headers["marimo-session-id"] == "session-1"
    assert headers["marimo-server-token"] == "secret"
    assert headers["authorization"] == "Bearer secret"


def test_post_json_raises_with_http_response_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="nope")

    _install_mock_transport(monkeypatch, handler)

    with pytest.raises(RuntimeError, match="POST /api/demo failed with HTTP 403: nope"):
        post_json(
            "http://localhost:2718",
            "/api/demo",
            body=None,
            headers=None,
            token=None,
            timeout=5,
        )


def test_execute_scratchpad_reads_sse_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["headers"] = request.headers
        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200,
            text=(
                'event: stdout\ndata: {"data": "hello"}\n\n'
                'event: stderr\ndata: {"data": "warn"}\n\n'
                'event: done\ndata: {"success": true, "output": {"path": "bundle"}}\n\n'
            ),
            headers={"Content-Type": "text/event-stream"},
        )

    _install_mock_transport(monkeypatch, handler)

    result = execute_scratchpad(
        "http://localhost:2718",
        "session-1",
        "print('hello')",
        token="secret",
        timeout=5,
    )

    assert result.stdout == ["hello"]
    assert result.stderr == ["warn"]
    assert result.output == {"path": "bundle"}
    assert captured["path"] == "/api/kernel/execute"
    assert captured["json"] == {"code": "print('hello')"}
    headers = captured["headers"]
    assert headers["accept"] == "text/event-stream"
    assert headers["marimo-session-id"] == "session-1"
    assert headers["marimo-server-token"] == "secret"


def test_can_import_returns_probe_payload_without_masking_http_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    probe_marker = "TEST_IMPORT_"
    monkeypatch.setattr(scratchpad_impl, "marker", lambda _kind: probe_marker)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=(
                f'event: stdout\ndata: {{"data": "{probe_marker}false"}}\n\n'
                'event: done\ndata: {"success": true, "output": null}\n\n'
            ),
            headers={"Content-Type": "text/event-stream"},
        )

    _install_mock_transport(monkeypatch, handler)

    assert not can_import(
        "http://localhost:2718",
        "session-1",
        "moexport",
        token=None,
    )
    assert len(requests) == 1


def test_can_import_propagates_http_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="kernel unavailable")

    _install_mock_transport(monkeypatch, handler)

    with pytest.raises(RuntimeError, match="HTTP 500: kernel unavailable"):
        can_import(
            "http://localhost:2718",
            "session-1",
            "moexport",
            token=None,
        )


def test_ensure_runtime_posts_requested_install_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    imports = iter([False, True])

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["headers"] = request.headers
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={})

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr(
        runtime_impl,
        "can_import",
        lambda *_args, **_kwargs: next(imports),
    )

    runtime_impl.ensure_runtime(
        server="http://localhost:2718",
        session_id="session-1",
        package="moexport @ file:///repo/packages/capture",
        module="moexport",
        manager="pip",
        source="server",
        force=False,
        token=None,
        timeout_ms=2500,
        poll_interval_ms=250,
    )

    assert captured["path"] == "/api/kernel/install_missing_packages"
    assert captured["headers"]["marimo-session-id"] == "session-1"
    assert captured["json"] == {
        "manager": "pip",
        "source": "server",
        "versions": {"moexport @ file:///repo/packages/capture": ""},
    }
