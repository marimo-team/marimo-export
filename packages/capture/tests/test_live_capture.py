from __future__ import annotations

import json
import re
from typing import Any, cast

import httpx
import pytest

import moexport as mox
import moexport.live_capture as live_capture
from moexport.live_capture import (
    LiveCapture,
    ScratchpadResult,
    execute_scratchpad,
    notebook_matches,
    parse_sse,
    post_json,
)
from moexport.request import resolve_export_request


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


def test_resolve_export_request_is_top_level_api() -> None:
    assert mox.resolve_export_request is resolve_export_request


def test_live_capture_accepts_preinstalled_runtime_and_export_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    spec = mox.parse_export_spec(
        {
            "values": {
                "summary": {
                    "source": {"def": "summary"},
                    "artifacts": ["json"],
                }
            }
        }
    )

    monkeypatch.setattr(
        live_capture,
        "resolve_session",
        lambda **_kwargs: {"sessionId": "session-1", "path": "notebook.py"},
    )
    monkeypatch.setattr(live_capture, "can_import", lambda *_args, **_kwargs: True)

    def execute(*_args: Any, **kwargs: Any) -> ScratchpadResult:
        code = kwargs.get("code") or _args[2]
        captured["code"] = code
        marker = re.search(r"__MOEXPORT_CAPTURE_\d+__", code)
        assert marker is not None
        return ScratchpadResult(
            stdout=[
                marker.group(0)
                + json.dumps(
                    {
                        "bundle_path": "out/bundles/sha256-demo",
                        "manifest_path": "out/bundles/sha256-demo/manifest.json",
                        "invocation_path": "out/bundles/sha256-demo/traces/run.json",
                        "invocation_index_path": "out/bundles/sha256-demo/traces/index.json",
                        "manifest": {},
                        "invocation": {},
                    }
                )
            ],
            stderr=[],
        )

    monkeypatch.setattr(live_capture, "execute_scratchpad", execute)

    result = LiveCapture("http://localhost:2718", runtime="preinstalled").export(
        spec,
        to="public/export",
    )

    assert result["session"]["sessionId"] == "session-1"
    assert '\\"artifacts\\"' in captured["code"]


def test_live_capture_rejects_invalid_runtime_before_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        live_capture,
        "resolve_session",
        lambda **_kwargs: {"sessionId": "session-1", "path": "notebook.py"},
    )

    capture = LiveCapture("http://localhost:2718", runtime=cast(Any, "bad"))

    with pytest.raises(TypeError, match="runtime must be 'preinstalled'"):
        capture.export(
            {
                "values": {
                    "summary": {
                        "source": {"def": "summary"},
                        "artifacts": ["json"],
                    }
                }
            }
        )
