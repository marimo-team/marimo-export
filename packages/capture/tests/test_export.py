from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import moexport as mox
import pytest
from marimo._ast.compiler import compile_cell
from marimo._runtime.dataflow import DirectedGraph
from marimo._types.ids import CellId_t
from moexport.artifacts import Artifact, ArtifactData
from moexport.bundle.schema import BundleManifest, NotebookRecord
from moexport.exporters import ExporterContext

package_capture = mox.capture
export_module = importlib.import_module("moexport.export")
request_module = importlib.import_module("moexport.request")
target_module = importlib.import_module("moexport.evaluate._target")


def run(coro):
    return asyncio.run(coro)


def _blob_files(root: Path) -> list[Path]:
    blob_root = root / "blobs"
    return [path for path in blob_root.rglob("*") if path.is_file()]


def _scenario(manifest: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    return next(
        scenario for scenario in manifest["scenarios"] if scenario["id"] == scenario_id
    )


def _format_record(
    manifest: dict[str, Any],
    scenario_id: str,
    value_name: str,
    format_name: str,
) -> dict[str, Any]:
    return _scenario(manifest, scenario_id)["values"][value_name][format_name]


def _assert_notebook_source(
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


def _invocation_scenario(
    invocation: dict[str, Any],
    scenario_id: str,
) -> dict[str, Any]:
    return next(
        scenario
        for scenario in invocation["scenarios"]
        if scenario["id"] == scenario_id
    )


def _install_test_exporters() -> None:
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


def test_capture_materializes_cell_output_for_the_active_scenario(
    tmp_path: Path,
    monkeypatch,
) -> None:
    graph = DirectedGraph()
    graph.register_cell(
        CellId_t("config"),
        compile_cell("symbols = ['AAPL']", cell_id=CellId_t("config")),
    )
    graph.register_cell(
        CellId_t("display"),
        compile_cell("symbols[0]", cell_id=CellId_t("display")),
    )

    class FakeContext:
        filename = None
        cell_id = CellId_t("__current__")

        def __init__(self) -> None:
            self.graph = graph
            self.globals = {"symbols": ["AAPL"]}

        def with_cell_id(self, cid: CellId_t):
            del cid
            return nullcontext()

    ctx = FakeContext()
    monkeypatch.setattr(request_module, "get_context", lambda: ctx)
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    result = run(
        export_module.capture(
            {
                "scenarios": [{"id": "override", "state": {"symbols": ["MSFT"]}}],
                "values": {
                    "title": {
                        "source": {"cell": {"index": 1}},
                        "formats": ["text"],
                    }
                },
            },
            to=tmp_path / "scenario",
        )
    )

    def artifact_text(result: Any) -> str:
        artifact = result.manifest["scenarios"][0]["values"]["title"]["text"]
        entry = artifact["data"]["entry"]
        blob = artifact["data"]["files"][entry]
        return (Path(result.bundle_path).parent.parent / blob["href"]).read_text()

    assert result.manifest["values"]["title"]["source"] == {
        "type": "cell_output",
        "cell": {"index": 1},
        "on_error": "raise",
    }
    assert artifact_text(result) == "MSFT"


def test_capture_writes_manifest_formats_and_deduped_blobs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_test_exporters()
    notebook = tmp_path / "finance.py"
    notebook.write_text("# notebook")

    async def fake_evaluate(
        target: str,
        definition_overrides: list[dict[str, Any]],
        *,
        object_patches: list[dict[str, Any]] | None = None,
    ):
        del target, definition_overrides, object_patches
        return {
            "target": "target",
            "results": [
                {
                    "value": {"title": "same"},
                    "value_preview": "{'title': 'same'}",
                    "metadata": {
                        "target": {"root_names": ["title"]},
                        "graph": {"nodes": [{"cell_id": "a", "status": "inactive"}]},
                        "execution": {"stats": {"executed": 0}},
                    },
                },
                {
                    "value": {"title": "same"},
                    "value_preview": "{'title': 'same'}",
                    "metadata": {
                        "target": {"root_names": ["title"]},
                        "graph": {"nodes": [{"cell_id": "b", "status": "executed"}]},
                        "execution": {"stats": {"executed": 1}},
                    },
                },
            ],
            "metadata": {
                "batch": {"result_count": 2, "cache_scope": "call"},
                "execution": {"stats": {"executed": 0}},
            },
        }

    monkeypatch.setattr(export_module, "evaluate_plan", fake_evaluate)
    monkeypatch.setattr(
        request_module,
        "get_context",
        lambda: SimpleNamespace(filename=str(notebook)),
    )

    result = run(
        package_capture(
            {
                "scenarios": [
                    {"id": "default"},
                    {"id": "wide-chart", "state": {"chart_width": 1200}},
                ],
                "values": {
                    "title": {
                        "source": {"def": "title"},
                        "formats": {
                            "text": {
                                "export": {
                                    "type": "ref",
                                    "ref": "test_exporters:text",
                                }
                            }
                        },
                    }
                },
            },
            to=tmp_path / "bundle",
        )
    )

    bundle_path = Path(result.bundle_path)
    output_root = bundle_path.parent.parent
    root_index = json.loads((output_root / "index.json").read_text())
    manifest = json.loads((bundle_path / "manifest.json").read_text())
    invocation = json.loads(Path(result.invocation_path).read_text())
    invocation_index = json.loads(Path(result.invocation_index_path).read_text())
    first_format = _format_record(manifest, "default", "title", "text")
    second_format = _format_record(manifest, "wide-chart", "title", "text")
    first_blob = first_format["data"]["files"]["value"]
    second_blob = second_format["data"]["files"]["value"]

    assert result.manifest_path == str(bundle_path / "manifest.json")
    assert root_index["schema"] == "moexport.root_index.v1"
    assert root_index["latest"]["id"] == manifest["id"]
    assert root_index["latest"]["manifest_href"] == (
        f"bundles/{manifest['id']}/manifest.json"
    )
    assert root_index["bundles"] == [root_index["latest"]]
    assert set(manifest) == {
        "schema",
        "version",
        "id",
        "sha256",
        "notebook",
        "scenario_set",
        "capture",
        "values",
        "scenarios",
        "provenance",
    }
    assert manifest["schema"] == "moexport.bundle.v1"
    BundleManifest.model_validate(manifest)
    assert manifest["version"] == 1
    assert manifest["id"].startswith("sha256-")
    assert len(manifest["sha256"]) == 64
    _assert_notebook_source(output_root, manifest)
    assert invocation["notebook"] == manifest["notebook"]
    assert manifest["scenario_set"]["id"].startswith("sha256-")
    assert len(manifest["scenario_set"]["sha256"]) == 64
    assert manifest["capture"]["id"].startswith("sha256-")
    assert len(manifest["capture"]["request_sha256"]) == 64
    assert manifest["values"]["title"] == {
        "source": {"type": "definition", "name": "title"},
        "formats": ["text"],
    }
    assert manifest["provenance"]["invocations_index_href"] == (
        f"bundles/{manifest['id']}/traces/index.json"
    )
    assert len(manifest["provenance"]["source_spec_sha256"]) == 64
    source_spec = manifest["provenance"]["source_spec"]
    assert "bundle" not in source_spec
    assert source_spec["scenarios"] == [
        {"id": "default"},
        {"id": "wide-chart", "state": {"chart_width": 1200}},
    ]
    assert source_spec["values"]["title"]["source"] == {
        "type": "definition",
        "name": "title",
    }
    assert result.invocation_index_path == str(
        output_root / manifest["provenance"]["invocations_index_href"]
    )
    assert result.invocation == invocation
    assert invocation["schema"] == "moexport.invocation.v1"
    assert invocation["bundle"]["id"] == manifest["id"]
    assert (
        invocation["bundle"]["manifest_href"]
        == f"bundles/{manifest['id']}/manifest.json"
    )
    assert invocation["capture"] == manifest["capture"]
    assert invocation["source_spec"] == {
        "sha256": manifest["provenance"]["source_spec_sha256"],
        "spec": manifest["provenance"]["source_spec"],
    }
    assert _invocation_scenario(invocation, "default")["trace"]["graph"]["nodes"] == [
        {"cell_id": "a", "status": "inactive"}
    ]
    assert _invocation_scenario(invocation, "wide-chart")["trace"]["execution"][
        "stats"
    ] == {"executed": 1}
    assert invocation_index["bundle"]["id"] == manifest["id"]
    assert invocation_index["invocations"] == [
        {
            "id": invocation["id"],
            "sha256": invocation["sha256"],
            "created_at": invocation["created_at"],
            "href": f"bundles/{manifest['id']}/traces/{invocation['id']}.json",
        }
    ]
    assert first_format["format_id"] == "text.v1"
    assert first_blob["href"] == second_blob["href"]
    assert first_blob["href"].startswith("blobs/sha256/")
    assert (output_root / first_blob["href"]).read_text() == "same"
    assert len(_blob_files(output_root)) == 1


def test_capture_resolves_code_state_before_writing_outputs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    notebook = tmp_path / "finance.py"
    notebook.write_text("# notebook")
    graph = DirectedGraph()
    graph.register_cell(
        CellId_t("summary"),
        compile_cell(
            "summary = f'{base_width}x{chart_width}'",
            cell_id=CellId_t("summary"),
        ),
    )

    class FakeContext:
        filename = str(notebook)
        cell_id = CellId_t("__current__")

        def __init__(self) -> None:
            self.graph = graph
            self.globals = {}

        def with_cell_id(self, cid: CellId_t):
            del cid
            return nullcontext()

    ctx = FakeContext()
    monkeypatch.setattr(request_module, "get_context", lambda: ctx)
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    result = run(
        export_module.capture(
            {
                "scenarios": [
                    {
                        "id": "computed",
                        "state": {
                            "base_width": 500,
                            "chart_width": {
                                "code": "base_width * 2",
                            },
                        },
                    }
                ],
                "values": {
                    "summary": {
                        "source": {"def": "summary"},
                        "formats": {
                            "json": {
                                "export": {
                                    "type": "code",
                                    "code": """
import json

def export(value, ctx, **options):
    blob = ctx.write_blob(
        "summary.json",
        json.dumps(value).encode(),
        media_type="application/json",
    )
    return {
        "format_id": "summary.v1",
        "media_type": "application/json",
        "data": {
            "type": "bundle",
            "files": {"data": blob},
            "entry": "data",
        },
        "metadata": {"scenario": ctx.scenario_id},
    }
""",
                                }
                            }
                        },
                    }
                },
            },
            to=tmp_path / "bundle",
        )
    )

    manifest = result.manifest
    artifact = _format_record(manifest, "computed", "summary", "json")
    blob = artifact["data"]["files"]["data"]
    output_root = Path(result.bundle_path).parent.parent
    blob_value = json.loads((output_root / blob["href"]).read_text())
    assert blob_value == "500x1000"
    scenario = _scenario(manifest, "computed")
    assert scenario["state"]["chart_width"] == 1000
    assert scenario["declared_state"]["chart_width"] == {
        "code": "base_width * 2",
    }


def test_capture_applies_dotted_state_keys_to_object_attributes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_test_exporters()
    notebook = tmp_path / "finance.py"
    notebook.write_text("# notebook")
    graph = DirectedGraph()
    graph.register_cell(
        CellId_t("selector"),
        compile_cell(
            """
class Selector:
    def __init__(self):
        self.config = {"label": "Default"}

selector = Selector()
""",
            cell_id=CellId_t("selector"),
        ),
    )
    graph.register_cell(
        CellId_t("title"),
        compile_cell(
            "title = f'{selector.config[\"label\"]}: {symbol}'",
            cell_id=CellId_t("title"),
        ),
    )

    class FakeContext:
        filename = str(notebook)
        cell_id = CellId_t("__current__")

        def __init__(self) -> None:
            self.graph = graph
            self.globals = {}

        def with_cell_id(self, cid: CellId_t):
            del cid
            return nullcontext()

    ctx = FakeContext()
    monkeypatch.setattr(request_module, "get_context", lambda: ctx)
    monkeypatch.setattr(target_module, "get_context", lambda: ctx)

    result = run(
        export_module.capture(
            {
                "scenarios": [
                    {
                        "id": "patched",
                        "state": {
                            "symbol": "AAPL",
                            "selector.config": {"label": "Symbols"},
                        },
                    }
                ],
                "values": {
                    "title": {
                        "source": {"def": "title"},
                        "formats": ["text"],
                    }
                },
            },
            to=tmp_path / "bundle",
        )
    )

    assert result.manifest["scenarios"][0]["state"] == {
        "symbol": "AAPL",
        "selector.config": {"label": "Symbols"},
    }
    artifact = _format_record(result.manifest, "patched", "title", "text")
    blob = artifact["data"]["files"][artifact["data"]["entry"]]
    output_root = Path(result.bundle_path).parent.parent
    assert (output_root / blob["href"]).read_text() == "Symbols: AAPL"
    assert _invocation_scenario(result.invocation, "patched")["trace"]["state"][
        "applied_object_patches"
    ] == [
        {
            "target": "selector.config",
            "root": "selector",
            "value_preview": "{'label': 'Symbols'}",
        }
    ]


def test_capture_rejects_non_json_scenario_state_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    notebook = tmp_path / "finance.py"
    notebook.write_text("# notebook")

    async def fake_evaluate(
        target: str,
        definition_overrides: Any = None,
        *,
        object_patches: Any = None,
    ):
        del definition_overrides, object_patches
        if target == "object()":
            return {
                "target": target,
                "results": [{"value": object()}],
                "metadata": {"batch": {"result_count": 1}},
            }
        return {
            "target": target,
            "results": [{"value": {"summary": "ok"}}],
            "metadata": {"batch": {"result_count": 1}},
        }

    monkeypatch.setattr(export_module, "evaluate_plan", fake_evaluate)
    monkeypatch.setattr(
        request_module,
        "get_context",
        lambda: SimpleNamespace(filename=str(notebook)),
    )

    with pytest.raises(ValueError, match="scenario state values must be JSON"):
        run(
            export_module.capture(
                {
                    "scenarios": [
                        {
                            "id": "computed",
                            "state": {
                                "opaque": {
                                    "code": "object()",
                                },
                            },
                        }
                    ],
                    "values": {
                        "summary": {
                            "source": {"def": "summary"},
                            "formats": ["json"],
                        }
                    },
                },
                to=tmp_path / "bundle",
            )
        )


def test_capture_rejects_embedded_format_payloads(
    tmp_path: Path,
    monkeypatch,
) -> None:
    notebook = tmp_path / "finance.py"
    notebook.write_text("# notebook")

    async def fake_evaluate(
        target: str,
        definition_overrides: list[dict[str, Any]],
        *,
        object_patches: list[dict[str, Any]] | None = None,
    ):
        del target, definition_overrides, object_patches
        return {
            "target": "target",
            "results": [{"value": {"summary": "ok"}}],
            "metadata": {"batch": {"result_count": 1}, "execution": {}},
        }

    monkeypatch.setattr(export_module, "evaluate_plan", fake_evaluate)
    monkeypatch.setattr(
        request_module,
        "get_context",
        lambda: SimpleNamespace(filename=str(notebook)),
    )

    with pytest.raises(TypeError, match="type='bundle'"):
        run(
            export_module.capture(
                {
                    "values": {
                        "summary": {
                            "source": {"def": "summary"},
                            "formats": {
                                "json": {
                                    "export": {
                                        "type": "code",
                                        "code": """
def export(value, ctx, **options):
    return {
        "format_id": "summary.v1",
        "media_type": "application/json",
        "data": {"type": "embedded", "value": value},
        "metadata": None,
    }
""",
                                    }
                                }
                            },
                        }
                    },
                },
                to=tmp_path / "bundle",
            )
        )
    assert not (tmp_path / "bundle" / "manifest.json").exists()
    assert not (tmp_path / "bundle" / "index.json").exists()


def test_capture_default_bundle_path_uses_marimo_output_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_test_exporters()
    notebook = tmp_path / "finance.py"
    notebook.write_text("# notebook")

    async def fake_evaluate(
        target: str,
        definition_overrides: list[dict[str, Any]],
        *,
        object_patches: list[dict[str, Any]] | None = None,
    ):
        del target, definition_overrides, object_patches
        return {
            "target": "target",
            "results": [{"value": {"title": "same"}}],
            "metadata": {
                "batch": {"result_count": 1, "cache_scope": "call"},
                "execution": {},
            },
        }

    monkeypatch.setattr(export_module, "evaluate_plan", fake_evaluate)
    monkeypatch.setattr(
        request_module,
        "get_context",
        lambda: SimpleNamespace(filename=str(notebook)),
    )

    result = run(
        export_module.capture(
            {
                "values": {
                    "title": {
                        "source": {"def": "title"},
                        "formats": {
                            "text": {
                                "export": {
                                    "type": "ref",
                                    "ref": "test_exporters:text",
                                }
                            }
                        },
                    }
                },
            }
        )
    )

    bundle_path = Path(result.bundle_path)
    assert bundle_path.parent == tmp_path / "__marimo__" / "static-export" / "bundles"
    assert bundle_path.name.startswith("sha256-")
    _assert_notebook_source(bundle_path.parent.parent, result.manifest)
    assert Path(result.manifest_path).exists()
    assert Path(result.invocation_index_path).exists()
    assert Path(result.invocation_path).exists()


def test_capture_uses_live_notebook_path_over_spec_notebook(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_test_exporters()
    live_notebook = tmp_path / "live.py"
    live_notebook.write_text("# notebook")

    async def fake_evaluate(
        target: str,
        definition_overrides: list[dict[str, Any]],
        *,
        object_patches: list[dict[str, Any]] | None = None,
    ):
        del target, definition_overrides, object_patches
        return {
            "target": "target",
            "results": [{"value": {"title": "same"}}],
            "metadata": {
                "batch": {"result_count": 1, "cache_scope": "call"},
                "execution": {},
            },
        }

    monkeypatch.setattr(export_module, "evaluate_plan", fake_evaluate)
    monkeypatch.setattr(
        request_module,
        "get_context",
        lambda: SimpleNamespace(filename=str(live_notebook)),
    )

    result = run(
        export_module.capture(
            {
                "values": {
                    "title": {
                        "source": {"def": "title"},
                        "formats": {
                            "text": {
                                "export": {
                                    "type": "ref",
                                    "ref": "test_exporters:text",
                                }
                            }
                        },
                    }
                },
            },
            to=tmp_path / "bundle",
        )
    )

    _assert_notebook_source(
        Path(result.bundle_path).parent.parent,
        result.manifest,
        name="live.py",
    )


def test_capture_default_bundle_path_groups_same_scenario_set(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_test_exporters()
    notebook = tmp_path / "finance.py"
    notebook.write_text("# notebook")

    async def fake_evaluate(
        target: str,
        definition_overrides: list[dict[str, Any]],
        *,
        object_patches: list[dict[str, Any]] | None = None,
    ):
        del target
        return {
            "target": "target",
            "results": [
                {"value": {"title": "same", "summary": "same"}}
                for _ in definition_overrides
            ],
            "metadata": {
                "batch": {
                    "result_count": len(definition_overrides),
                    "cache_scope": "call",
                },
                "execution": {},
            },
        }

    monkeypatch.setattr(export_module, "evaluate_plan", fake_evaluate)
    monkeypatch.setattr(
        request_module,
        "get_context",
        lambda: SimpleNamespace(filename=str(notebook)),
    )

    title_spec = {
        "values": {
            "title": {
                "source": {"def": "title"},
                "formats": {
                    "text": {
                        "export": {
                            "type": "ref",
                            "ref": "test_exporters:text",
                        }
                    }
                },
            }
        },
    }
    title_and_summary_spec = {
        "values": {
            **title_spec["values"],
            "summary": {
                "source": {"def": "summary"},
                "formats": {
                    "text": {
                        "export": {
                            "type": "ref",
                            "ref": "test_exporters:text",
                        }
                    }
                },
            },
        },
    }

    first = run(export_module.capture(title_spec))
    second = run(export_module.capture(title_and_summary_spec))

    first_path = Path(first.bundle_path)
    second_path = Path(second.bundle_path)
    output_root = first_path.parent.parent
    assert output_root == second_path.parent.parent
    assert first_path != second_path
    assert first.manifest["scenario_set"] == second.manifest["scenario_set"]
    assert first.manifest["notebook"] == second.manifest["notebook"]
    assert (
        first.manifest["capture"]["request_sha256"]
        != second.manifest["capture"]["request_sha256"]
    )
    assert len(_blob_files(output_root)) == 1


def test_capture_default_bundle_path_shares_blobs_across_renamed_values(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_test_exporters()
    notebook = tmp_path / "finance.py"
    notebook.write_text("# notebook")

    async def fake_evaluate(
        target: str,
        definition_overrides: list[dict[str, Any]],
        *,
        object_patches: list[dict[str, Any]] | None = None,
    ):
        del definition_overrides, object_patches
        value_name = "renamed_title" if "renamed_title" in target else "title"
        return {
            "target": target,
            "results": [{"value": {value_name: "same"}}],
            "metadata": {
                "batch": {"result_count": 1, "cache_scope": "call"},
                "execution": {},
            },
        }

    monkeypatch.setattr(export_module, "evaluate_plan", fake_evaluate)
    monkeypatch.setattr(
        request_module,
        "get_context",
        lambda: SimpleNamespace(filename=str(notebook)),
    )

    def spec(value_name: str) -> dict[str, Any]:
        return {
            "values": {
                value_name: {
                    "source": {"def": "title"},
                    "formats": {
                        "text": {
                            "export": {
                                "type": "ref",
                                "ref": "test_exporters:text",
                            }
                        }
                    },
                }
            },
        }

    first = run(export_module.capture(spec("title")))
    second = run(export_module.capture(spec("renamed_title")))

    first_path = Path(first.bundle_path)
    second_path = Path(second.bundle_path)
    output_root = first_path.parent.parent

    assert output_root == second_path.parent.parent
    assert first_path != second_path
    assert first.manifest["id"] != second.manifest["id"]
    assert len(_blob_files(output_root)) == 1

    first_format = _format_record(first.manifest, "default", "title", "text")
    second_format = _format_record(second.manifest, "default", "renamed_title", "text")
    assert (
        first_format["data"]["files"]["value"]["href"]
        == second_format["data"]["files"]["value"]["href"]
    )
    href = first_format["data"]["files"]["value"]["href"]
    assert (output_root / href).read_text() == "same"
    assert first_format["data"]["files"]["value"]["href"].startswith("blobs/sha256/")


def test_capture_records_trace_without_changing_bundle_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_test_exporters()
    notebook = tmp_path / "finance.py"
    notebook.write_text("# notebook")
    run_count = 0

    async def fake_evaluate(
        target: str,
        definition_overrides: list[dict[str, Any]],
        *,
        object_patches: list[dict[str, Any]] | None = None,
    ):
        nonlocal run_count
        run_count += 1
        del target, definition_overrides, object_patches
        return {
            "target": "target",
            "results": [
                {
                    "value": {"title": "same"},
                    "value_preview": "{'title': 'same'}",
                    "metadata": {
                        "target": {"root_names": ["title"]},
                        "graph": {
                            "nodes": [{"cell_id": "cell", "status": f"run-{run_count}"}]
                        },
                        "execution": {
                            "elapsed_ms": run_count,
                            "stats": {"executed": run_count},
                        },
                    },
                }
            ],
            "metadata": {"batch": {"result_count": 1}, "execution": {}},
        }

    monkeypatch.setattr(export_module, "evaluate_plan", fake_evaluate)
    monkeypatch.setattr(
        request_module,
        "get_context",
        lambda: SimpleNamespace(filename=str(notebook)),
    )

    spec = {
        "values": {
            "title": {
                "source": {"def": "title"},
                "formats": {
                    "text": {
                        "export": {
                            "type": "ref",
                            "ref": "test_exporters:text",
                        }
                    }
                },
            }
        },
    }

    first = run(export_module.capture(spec, to=tmp_path / "bundle"))
    second = run(export_module.capture(spec, to=tmp_path / "bundle"))

    assert first.bundle_path == second.bundle_path
    assert first.manifest["id"] == second.manifest["id"]
    assert first.invocation_path != second.invocation_path
    BundleManifest.model_validate(second.manifest)
    assert _invocation_scenario(second.invocation, "default")["trace"]["graph"][
        "nodes"
    ] == [{"cell_id": "cell", "status": "run-2"}]
    invocation_index = json.loads(Path(second.invocation_index_path).read_text())
    assert [item["id"] for item in invocation_index["invocations"]] == [
        first.invocation["id"],
        second.invocation["id"],
    ]


def test_capture_default_bundle_path_separates_different_scenario_sets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_test_exporters()
    notebook = tmp_path / "finance.py"
    notebook.write_text("# notebook")

    async def fake_evaluate(
        target: str,
        definition_overrides: list[dict[str, Any]],
        *,
        object_patches: list[dict[str, Any]] | None = None,
    ):
        del target
        return {
            "target": "target",
            "results": [{"value": {"title": "same"}} for _ in definition_overrides],
            "metadata": {
                "batch": {
                    "result_count": len(definition_overrides),
                    "cache_scope": "call",
                },
                "execution": {},
            },
        }

    monkeypatch.setattr(export_module, "evaluate_plan", fake_evaluate)
    monkeypatch.setattr(
        request_module,
        "get_context",
        lambda: SimpleNamespace(filename=str(notebook)),
    )

    base_spec = {
        "values": {
            "title": {
                "source": {"def": "title"},
                "formats": {
                    "text": {
                        "export": {
                            "type": "ref",
                            "ref": "test_exporters:text",
                        }
                    }
                },
            }
        },
    }
    wide_spec = {
        **base_spec,
        "scenarios": [
            {"id": "default"},
            {"id": "wide", "state": {"chart_width": 1200}},
        ],
    }

    first = run(export_module.capture(base_spec))
    second = run(export_module.capture(wide_spec))

    first_path = Path(first.bundle_path)
    second_path = Path(second.bundle_path)
    assert first_path.parent.parent == second_path.parent.parent
    assert first_path != second_path
    assert len(_blob_files(first_path.parent.parent)) == 1
    assert first.manifest["scenario_set"] != second.manifest["scenario_set"]
    assert first.manifest["notebook"] == second.manifest["notebook"]


def test_capture_scenario_set_grouping_is_order_insensitive(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_test_exporters()
    notebook = tmp_path / "finance.py"
    notebook.write_text("# notebook")

    async def fake_evaluate(
        target: str,
        definition_overrides: list[dict[str, Any]],
        *,
        object_patches: list[dict[str, Any]] | None = None,
    ):
        del target
        return {
            "target": "target",
            "results": [
                {"value": {"title": str(index)}}
                for index, _ in enumerate(definition_overrides)
            ],
            "metadata": {
                "batch": {
                    "result_count": len(definition_overrides),
                    "cache_scope": "call",
                },
                "execution": {},
            },
        }

    monkeypatch.setattr(export_module, "evaluate_plan", fake_evaluate)
    monkeypatch.setattr(
        request_module,
        "get_context",
        lambda: SimpleNamespace(filename=str(notebook)),
    )

    values = {
        "title": {
            "source": {"def": "title"},
            "formats": {
                "text": {
                    "export": {
                        "type": "ref",
                        "ref": "test_exporters:text",
                    }
                }
            },
        }
    }
    first = run(
        export_module.capture(
            {
                "scenarios": [
                    {"id": "wide", "state": {"chart_width": 1200}},
                    {"id": "default"},
                ],
                "values": values,
            }
        )
    )
    second = run(
        export_module.capture(
            {
                "scenarios": [
                    {"id": "default"},
                    {"id": "wide", "state": {"chart_width": 1200}},
                ],
                "values": values,
            }
        )
    )

    assert first.bundle_path == second.bundle_path
    assert first.manifest["scenario_set"] == second.manifest["scenario_set"]
    assert [scenario["id"] for scenario in first.manifest["scenarios"]] == [
        "default",
        "wide",
    ]
