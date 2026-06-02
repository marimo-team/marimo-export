from __future__ import annotations

import json
import re
from typing import Any, cast

import pytest

import moexport.client._client as client_impl
from moexport.client import ExportClient
from moexport.client._types import RuntimeInstall, ScratchpadResult, SessionInfo
from moexport.spec import parse_export_spec


def test_parse_export_spec_accepts_public_mapping() -> None:
    assert parse_export_spec(
        {"values": {"summary": {"source": "summary", "formats": ["json"]}}}
    )


def test_export_client_accepts_preinstalled_runtime_and_export_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    spec = parse_export_spec(
        {
            "values": {
                "summary": {
                    "source": {"def": "summary"},
                    "formats": ["json"],
                }
            }
        }
    )

    monkeypatch.setattr(
        client_impl,
        "resolve_session",
        lambda **_kwargs: SessionInfo(session_id="session-1", path="notebook.py"),
    )
    monkeypatch.setattr(client_impl, "can_import", lambda *_args, **_kwargs: True)

    def execute(*_args: Any, **kwargs: Any) -> ScratchpadResult:
        code = kwargs.get("code") or _args[2]
        captured["code"] = code
        marker = re.search(r"__MOEXPORT_EXPORT_\d+__", code)
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

    monkeypatch.setattr(client_impl, "execute_scratchpad", execute)

    result = ExportClient("http://localhost:2718", runtime="preinstalled").export(
        spec,
        to="public/export",
        paths=["nogit/use-cases/metrics-readout"],
    )

    assert result.session_id == "session-1"
    assert result.session_path == "notebook.py"
    assert result.bundle_path == "out/bundles/sha256-demo"
    assert result.manifest_path.endswith("manifest.json")
    assert '\\"formats\\"' in captured["code"]
    assert "nogit/use-cases/metrics-readout" in captured["code"]


def test_archive_client_returns_bytes_media_type_and_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        client_impl,
        "resolve_session",
        lambda **_kwargs: SessionInfo(
            session_id="session-1",
            name="finance.py",
            path="/work/finance.py",
            initialization_id="init-1",
        ),
    )
    monkeypatch.setattr(client_impl, "can_import", lambda *_args, **_kwargs: True)

    def execute(*_args: Any, **kwargs: Any) -> ScratchpadResult:
        code = kwargs.get("code") or _args[2]
        marker = re.search(r"__MOEXPORT_ARCHIVE_\d+__", code)
        assert marker is not None
        return ScratchpadResult(stdout=[marker.group(0) + "emlwLWJ5dGVz"], stderr=[])

    monkeypatch.setattr(client_impl, "execute_scratchpad", execute)

    result = ExportClient("http://localhost:2718", runtime="preinstalled").archive(
        {"values": {"summary": {"source": {"def": "summary"}, "formats": ["json"]}}}
    )

    assert result.bytes == b"zip-bytes"
    assert result.media_type == "application/vnd.marimo.static-export+zip"
    assert result.session_id == "session-1"
    assert result.session_name == "finance.py"
    assert result.session_path == "/work/finance.py"
    assert result.session_initialization_id == "init-1"


def test_export_client_rejects_invalid_runtime_before_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        client_impl,
        "resolve_session",
        lambda **_kwargs: SessionInfo(session_id="session-1", path="notebook.py"),
    )

    client = ExportClient("http://localhost:2718", runtime=cast(Any, "bad"))

    with pytest.raises(TypeError, match="runtime must be 'preinstalled'"):
        client.export(
            {
                "values": {
                    "summary": {
                        "source": {"def": "summary"},
                        "formats": ["json"],
                    }
                }
            }
        )


def test_export_client_threads_runtime_install_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        client_impl,
        "resolve_session",
        lambda **_kwargs: SessionInfo(session_id="session-1", path="notebook.py"),
    )

    def ensure_runtime(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(client_impl, "ensure_runtime", ensure_runtime)
    monkeypatch.setattr(
        client_impl,
        "execute_scratchpad",
        lambda *_args, **_kwargs: ScratchpadResult(stdout=[""], stderr=[]),
    )

    client = ExportClient(
        "http://localhost:2718",
        runtime=RuntimeInstall(
            package="moexport @ file:///repo/packages/capture",
            module="moexport",
            manager="pip",
            source="server",
            force=True,
            timeout_ms=2500,
            poll_interval_ms=250,
        ),
    )

    with pytest.raises(RuntimeError, match="export marker"):
        client.export(
            {
                "values": {
                    "summary": {
                        "source": {"def": "summary"},
                        "formats": ["json"],
                    }
                }
            }
        )

    assert captured == {
        "server": "http://localhost:2718",
        "session_id": "session-1",
        "package": "moexport @ file:///repo/packages/capture",
        "module": "moexport",
        "manager": "pip",
        "source": "server",
        "force": True,
        "timeout_ms": 2500,
        "poll_interval_ms": 250,
        "token": None,
    }
