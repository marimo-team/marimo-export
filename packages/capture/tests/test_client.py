from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import textwrap
import types
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

import moexport.client._client as client_impl
from moexport.client._code import transport_spec
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
    assert_generated_code_runs_through_moexport(
        captured["code"],
        [
            {
                "kind": "capture",
                "spec": transport_spec(spec),
                "to": "public/export",
            }
        ],
    )
    assert transport_spec(spec)["values"]["readout"]["formats"] == [
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
    assert_generated_code_runs_through_moexport(
        captured["code"],
        [
            {
                "kind": "archive",
                "spec": transport_spec(
                    parse_export_spec(
                        {
                            "values": {
                                "summary": {
                                    "source": {"def": "summary"},
                                    "formats": ["json"],
                                }
                            }
                        }
                    )
                ),
            }
        ],
    )


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


def assert_generated_code_runs_through_moexport(
    code: str,
    expected_calls: list[dict[str, Any]],
) -> None:
    existing = {
        name: module
        for name, module in sys.modules.items()
        if name == "moexport" or name.startswith("moexport.")
    }
    original_path = list(sys.path)
    try:
        calls = run_generated_code_with_fake_moexport(code)
        assert calls == expected_calls
    finally:
        for name in list(sys.modules):
            if name == "moexport" or name.startswith("moexport."):
                del sys.modules[name]
        sys.modules.update(existing)
        sys.path[:] = original_path


def run_generated_code_with_fake_moexport(code: str) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="moexport-client-test-") as temp:
        temp_path = Path(temp)
        calls_path = temp_path / "calls.jsonl"
        package_dir = temp_path / "moexport"
        package_dir.mkdir()
        (package_dir / "__init__.py").write_text(
            f"""
import json
from pathlib import Path

CALLS_PATH = Path({str(calls_path)!r})

def _record(payload):
    with CALLS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, allow_nan=False) + "\\n")

class Result:
    bundle_path = "out/bundles/sha256-demo"
    manifest_path = "out/bundles/sha256-demo/manifest.json"
    invocation_path = "out/bundles/sha256-demo/traces/run.json"
    invocation_index_path = "out/bundles/sha256-demo/traces/index.json"
    manifest = {{}}
    invocation = {{}}

async def capture(spec, to=None):
    _record({{"kind": "capture", "spec": spec, "to": to}})
    return Result()
""",
            encoding="utf-8",
        )
        (package_dir / "archive.py").write_text(
            f"""
import base64
import json
from pathlib import Path

CALLS_PATH = Path({str(calls_path)!r})

async def emit_bundle_archive(spec, *, marker):
    with CALLS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({{"kind": "archive", "spec": spec}}, allow_nan=False) + "\\n")
    print(marker + base64.b64encode(b"zip-bytes").decode("ascii"))
""",
            encoding="utf-8",
        )

        sys.path.insert(0, temp)
        sys.modules["moexport"] = types.ModuleType("moexport")
        sys.modules["moexport.archive"] = types.ModuleType("moexport.archive")
        namespace: dict[str, Any] = {}
        exec("async def __run():\n" + textwrap.indent(code, "    "), namespace)
        asyncio.run(namespace["__run"]())
        return [
            json.loads(line)
            for line in calls_path.read_text(encoding="utf-8").splitlines()
            if line
        ]
