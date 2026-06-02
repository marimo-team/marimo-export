from __future__ import annotations

import asyncio
import hashlib
import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import moexport as mox
from moexport.artifacts import Artifact, ArtifactData
from moexport.bundle.schema import NotebookRecord
from moexport.exporters import ExporterContext

package_capture = mox.capture
export_module = importlib.import_module("moexport.export")
request_module = importlib.import_module("moexport.request")
target_module = importlib.import_module("moexport.evaluate._target")


def run(coro):
    return asyncio.run(coro)


def blob_files(root: Path) -> list[Path]:
    blob_root = root / "blobs"
    return [path for path in blob_root.rglob("*") if path.is_file()]


def scenario(manifest: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    return next(
        scenario for scenario in manifest["scenarios"] if scenario["id"] == scenario_id
    )


def format_record(
    manifest: dict[str, Any],
    scenario_id: str,
    value_name: str,
    format_name: str,
) -> dict[str, Any]:
    return scenario(manifest, scenario_id)["values"][value_name][format_name]


def assert_notebook_source(
    root: Path,
    manifest: dict[str, Any],
    *,
    name: str = "finance.py",
    content: bytes = b"# notebook",
    stored: bool = False,
) -> None:
    notebook = NotebookRecord.model_validate(manifest["notebook"])
    source = notebook.source

    assert notebook.name == name
    assert notebook.source_sha256 == hashlib.sha256(content).hexdigest()
    if stored:
        assert source is not None
        assert source.href.startswith("blobs/sha256/")
        assert source.media_type == "text/x-python"
        assert source.size == len(content)
        assert source.sha256 == hashlib.sha256(content).hexdigest()
        assert (root / source.href).read_bytes() == content
    else:
        assert source is None


def invocation_scenario(
    invocation: dict[str, Any],
    scenario_id: str,
) -> dict[str, Any]:
    return next(
        scenario
        for scenario in invocation["scenarios"]
        if scenario["id"] == scenario_id
    )


def install_test_exporters() -> None:
    module = ModuleType("test_exporters")

    def text(value: Any, ctx: ExporterContext, **options: Any) -> Artifact:
        del options
        blob = ctx.write_blob("value.txt", str(value).encode(), media_type="text/plain")
        return Artifact(
            format_id="text.v1",
            media_type="text/plain",
            data=ArtifactData(files={"value": blob}, entry="value"),
            metadata={
                "scenario": ctx.scenario_id,
                "value": ctx.value_name,
                "format": ctx.format_name,
            },
        )

    setattr(module, "text", text)
    sys.modules[module.__name__] = module
