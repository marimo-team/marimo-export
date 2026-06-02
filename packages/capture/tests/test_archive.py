from __future__ import annotations

import asyncio
import base64
import importlib
from io import BytesIO
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from zipfile import ZipFile

import moexport as mox
from moexport.archive import emit_bundle_archive
from moexport.artifacts import Artifact, ArtifactData
from moexport.exporters import ExporterContext

export_module = importlib.import_module("moexport.export")
request_module = importlib.import_module("moexport.request")


def run(coro):
    return asyncio.run(coro)


def _install_test_exporters() -> None:
    module = ModuleType("archive_test_exporters")

    def text(value: Any, ctx: ExporterContext, **options: Any) -> Artifact:
        del options
        blob = ctx.write_blob("value.txt", str(value).encode(), media_type="text/plain")
        return Artifact(
            format_id="text.v1",
            media_type="text/plain",
            data=ArtifactData(files={"value": blob}, entry="value"),
            metadata=None,
        )

    setattr(module, "text", text)
    sys.modules[module.__name__] = module


def test_archive_bundle_returns_zip_bytes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _install_test_exporters()
    notebook = tmp_path / "finance.py"
    notebook.write_text("# notebook")

    async def fake_evaluate(
        target: str,
        resolved_scenarios: list[dict[str, Any]],
        **_runtime_options: Any,
    ):
        return {
            "target": target,
            "results": [
                {
                    "value": {"title": "archived"},
                    "value_preview": "{'title': 'archived'}",
                    "metadata": {},
                }
            ],
            "metadata": {"batch": {"result_count": 1}},
        }

    monkeypatch.setattr(export_module, "evaluate_plan", fake_evaluate)
    monkeypatch.setattr(
        request_module,
        "get_context",
        lambda: SimpleNamespace(filename=str(notebook)),
    )

    result = run(
        mox.capture(
            {
                "scenarios": [{"id": "default"}],
                "values": {
                    "title": {
                        "source": {"def": "title"},
                        "formats": {
                            "text": {
                                "export": {
                                    "type": "ref",
                                    "ref": "archive_test_exporters:text",
                                }
                            }
                        },
                    }
                },
            }
        )
    )
    archive = mox.archive_bundle(result)

    assert mox.EXPORT_ARCHIVE_MEDIA_TYPE == "application/vnd.marimo.static-export+zip"
    assert isinstance(archive, bytes)
    assert result.manifest["id"].startswith("sha256-")

    with ZipFile(BytesIO(archive)) as zip_file:
        names = set(zip_file.namelist())
        index = json.loads(zip_file.read("index.json"))
        manifest_href = index["latest"]["manifest_href"]
        manifest = json.loads(zip_file.read(manifest_href))
        artifact = manifest["scenarios"][0]["values"]["title"]["text"]
        blob_href = artifact["data"]["files"]["value"]["href"]

        assert manifest == result.manifest
        assert manifest_href in names
        assert blob_href in names
        assert zip_file.read(blob_href) == b"archived"

    run(
        emit_bundle_archive(
            {
                "scenarios": [{"id": "default"}],
                "values": {
                    "title": {
                        "source": {"def": "title"},
                        "formats": {
                            "text": {
                                "export": {
                                    "type": "ref",
                                    "ref": "archive_test_exporters:text",
                                }
                            }
                        },
                    }
                },
            },
            marker="ARCHIVE:",
        )
    )
    line = capsys.readouterr().out.strip()

    assert line.startswith("ARCHIVE:")
    with ZipFile(BytesIO(base64.b64decode(line.removeprefix("ARCHIVE:")))) as zip_file:
        assert "index.json" in zip_file.namelist()
