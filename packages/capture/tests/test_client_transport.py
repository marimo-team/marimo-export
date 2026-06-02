from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from moexport.client._http import post_json
from moexport.client._scratchpad import execute_scratchpad, parse_sse
from moexport.client._session import notebook_matches


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
