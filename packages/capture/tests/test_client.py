from __future__ import annotations

import json
import re
from typing import Any, cast

import pytest
from pydantic import ValidationError

import moexport.client._client as client_impl
from moexport.client import Runtime, connect
from moexport.client._types import ScratchpadResult, SessionInfo
from moexport.spec import parse_export_spec


def test_parse_export_spec_accepts_public_mapping() -> None:
    assert parse_export_spec(
        {"values": {"summary": {"source": "summary", "formats": ["json"]}}}
    )


def test_export_client_validates_spec_before_resolving_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def resolve_session(**_kwargs: Any) -> SessionInfo:
        raise AssertionError("session resolution should not run")

    monkeypatch.setattr(client_impl, "resolve_session", resolve_session)

    with pytest.raises(ValidationError, match="values"):
        connect("http://localhost:2718").export({"values": {}})


def test_export_client_accepts_preinstalled_runtime_and_export_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    spec = parse_export_spec(
        {
            "values": {
                "readout": {
                    "source": {"def": "readout"},
                    "formats": [
                        {
                            "format": "metrics",
                            "export": {
                                "type": "ref",
                                "ref": "metrics_exporters:readout",
                            },
                            "options": {"title": "Metrics Readout"},
                        }
                    ],
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
    monkeypatch.setattr(client_impl, "marker", lambda kind: f"TEST_{kind}_")

    def execute(*_args: Any, **kwargs: Any) -> ScratchpadResult:
        code = kwargs.get("code") or _args[2]
        captured["code"] = code
        return ScratchpadResult(
            stdout=[
                "TEST_EXPORT_"
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

    result = connect("http://localhost:2718", runtime="preinstalled").export(
        spec,
        to="public/export",
        paths=["nogit/use-cases/metrics-readout"],
    )

    assert result.session_id == "session-1"
    assert result.session_path == "notebook.py"
    assert result.bundle_path == "out/bundles/sha256-demo"
    assert result.manifest_path.endswith("manifest.json")
    assert_fresh_import_code(captured["code"])
    assert transported_spec(captured["code"])["values"]["readout"]["formats"] == [
        {
            "format": "metrics",
            "export": {
                "type": "ref",
                "ref": "metrics_exporters:readout",
            },
            "options": {"title": "Metrics Readout"},
        }
    ]


def test_archive_client_returns_bytes_media_type_and_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

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
    monkeypatch.setattr(client_impl, "marker", lambda kind: f"TEST_{kind}_")

    def execute(*_args: Any, **kwargs: Any) -> ScratchpadResult:
        captured["code"] = kwargs.get("code") or _args[2]
        return ScratchpadResult(
            stdout=["TEST_ARCHIVE_" + "emlwLWJ5dGVz"],
            stderr=[],
        )

    monkeypatch.setattr(client_impl, "execute_scratchpad", execute)

    result = connect("http://localhost:2718", runtime="preinstalled").archive(
        {"values": {"summary": {"source": {"def": "summary"}, "formats": ["json"]}}}
    )

    assert result.bytes == b"zip-bytes"
    assert result.media_type == "application/vnd.marimo.static-export+zip"
    assert result.session_id == "session-1"
    assert result.session_name == "finance.py"
    assert result.session_path == "/work/finance.py"
    assert result.session_initialization_id == "init-1"
    assert_fresh_import_code(captured["code"])


def test_export_client_rejects_invalid_runtime_before_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(TypeError, match=r"Runtime\(package="):
        connect("http://localhost:2718", runtime=cast(Any, "bad"))

    def resolve_session(**_kwargs: Any) -> SessionInfo:
        raise AssertionError("session resolution should not run")

    monkeypatch.setattr(client_impl, "resolve_session", resolve_session)

    client = connect("http://localhost:2718")
    with pytest.raises(TypeError, match=r"Runtime\(package="):
        client.archive(
            {
                "values": {
                    "summary": {
                        "source": {"def": "summary"},
                        "formats": ["json"],
                    }
                }
            },
            runtime=cast(Any, "bad"),
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

    client = connect(
        "http://localhost:2718",
        runtime=Runtime(
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


def transported_spec(code: str) -> dict[str, Any]:
    match = re.search(r"^__moexport_spec = json\.loads\((.+)\)$", code, re.MULTILINE)
    if match is None:
        raise AssertionError("generated code did not load a serialized export spec")

    spec_json = json.loads(match.group(1))
    spec = json.loads(spec_json)
    assert parse_export_spec(spec)
    return spec


def assert_fresh_import_code(code: str) -> None:
    assert "for __moexport_module in list(sys.modules):" in code
    assert "__moexport_module == 'moexport'" in code
    assert "__moexport_module.startswith('moexport.')" in code
    assert "del sys.modules[__moexport_module]" in code
