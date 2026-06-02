from __future__ import annotations

import json
import re
from typing import Any, cast

import pytest

import moexport as mox
import moexport.client._client as client_impl
from moexport.client import ExportClient
from moexport.client._types import ScratchpadResult


def test_parse_spec_is_top_level_api() -> None:
    assert mox.parse_spec(
        {"values": {"summary": {"source": "summary", "formats": ["json"]}}}
    )


def test_export_client_accepts_preinstalled_runtime_and_export_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    spec = mox.parse_spec(
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
        lambda **_kwargs: {"sessionId": "session-1", "path": "notebook.py"},
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

    assert result.session["sessionId"] == "session-1"
    assert result.bundle_path == "out/bundles/sha256-demo"
    assert result.manifest_path.endswith("manifest.json")
    assert '\\"formats\\"' in captured["code"]
    assert "nogit/use-cases/metrics-readout" in captured["code"]


def test_export_client_rejects_invalid_runtime_before_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        client_impl,
        "resolve_session",
        lambda **_kwargs: {"sessionId": "session-1", "path": "notebook.py"},
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
