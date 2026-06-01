from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import moexport as mox
import pytest
from moexport.artifacts import Artifact, ArtifactData
from moexport.exporters import ExporterContext

package_export = mox.export
export_module = importlib.import_module("moexport.export")
request_module = importlib.import_module("moexport.request")


def run(coro):
    return asyncio.run(coro)


def _blob_files(root: Path) -> list[Path]:
    blob_root = root / "blobs"
    return [path for path in blob_root.rglob("*") if path.is_file()]


def _scenario(manifest: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    return next(
        scenario for scenario in manifest["scenarios"] if scenario["id"] == scenario_id
    )


def _artifact(
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
    source = manifest["notebook"]["source"]

    assert manifest["notebook"]["name"] == name
    assert manifest["notebook"]["source_sha256"] == hashlib.sha256(content).hexdigest()
    if stored:
        assert source["href"].startswith("blobs/sha256/")
        assert source["media_type"] == "text/x-python"
        assert source["size"] == len(content)
        assert source["sha256"] == hashlib.sha256(content).hexdigest()
        assert (root / source["href"]).read_bytes() == content
    else:
        assert source is None
    assert "source_size" not in manifest["notebook"]
    assert "path" not in manifest["notebook"]


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
            format="text.v1",
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


def test_export_writes_manifest_artifacts_and_deduped_blobs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_test_exporters()
    notebook = tmp_path / "finance.py"
    notebook.write_text("# notebook")
    calls: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]] | None]] = []

    async def fake_evaluate(
        target: str,
        definition_overrides: list[dict[str, Any]],
        *,
        object_patches: list[dict[str, Any]] | None = None,
    ):
        calls.append((target, definition_overrides, object_patches))
        return {
            "target": target,
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

    monkeypatch.setattr(export_module, "evaluate", fake_evaluate)
    monkeypatch.setattr(
        request_module,
        "get_context",
        lambda: SimpleNamespace(filename=str(notebook)),
    )

    result = run(
        package_export(
            {
                "bundle": str(tmp_path / "bundle"),
                "scenarios": [
                    {"id": "default"},
                    {"id": "wide-chart", "inputs": {"chart_width": 1200}},
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
            }
        )
    )

    bundle_path = Path(result.bundle_path)
    output_root = bundle_path.parent.parent
    root_index = json.loads((output_root / "index.json").read_text())
    manifest = json.loads((bundle_path / "manifest.json").read_text())
    invocation = json.loads(Path(result.invocation_path).read_text())
    invocation_index = json.loads(Path(result.invocation_index_path).read_text())
    first_artifact = _artifact(manifest, "default", "title", "text")
    second_artifact = _artifact(manifest, "wide-chart", "title", "text")
    first_blob = first_artifact["data"]["files"]["value"]
    second_blob = second_artifact["data"]["files"]["value"]

    assert calls == [
        (
            "{\n  'title': (title)\n}",
            [{}, {"chart_width": 1200}],
            [{}, {}],
        )
    ]
    assert result.manifest_path == str(bundle_path / "manifest.json")
    assert root_index["schema"] == "moexport.root_index.v1"
    assert root_index["latest"]["id"] == manifest["id"]
    assert root_index["latest"]["manifest_href"] == (
        f"bundles/{manifest['id']}/manifest.json"
    )
    assert root_index["bundles"] == [root_index["latest"]]
    assert list(manifest) == [
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
    ]
    assert manifest["schema"] == "moexport.bundle.v1"
    assert manifest["version"] == 1
    assert manifest["id"].startswith("sha256-")
    assert len(manifest["sha256"]) == 64
    _assert_notebook_source(output_root, manifest)
    assert invocation["notebook"] == manifest["notebook"]
    assert not (bundle_path / "notebook.py").exists()
    assert manifest["scenario_set"]["id"].startswith("sha256-")
    assert len(manifest["scenario_set"]["sha256"]) == 64
    assert manifest["capture"]["id"].startswith("sha256-")
    assert len(manifest["capture"]["request_sha256"]) == 64
    assert manifest["values"]["title"] == {
        "source": {"type": "definition", "name": "title"},
        "formats": ["text"],
    }
    assert "trace" not in _scenario(manifest, "default")
    assert manifest["provenance"]["invocations_index_href"] == (
        f"bundles/{manifest['id']}/traces/index.json"
    )
    assert len(manifest["provenance"]["source_spec_sha256"]) == 64
    source_spec = manifest["provenance"]["source_spec"]
    assert "notebook" not in source_spec
    assert source_spec["bundle"] == {"path": str(tmp_path / "bundle")}
    assert source_spec["scenarios"] == [
        {"id": "default"},
        {"id": "wide-chart", "inputs": {"chart_width": 1200}},
    ]
    assert source_spec["values"]["title"]["source"] == {
        "type": "definition",
        "name": "title",
    }
    assert "options" not in source_spec["values"]["title"]["formats"]["text"]
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
    assert first_artifact["format_id"] == "text.v1"
    assert first_blob["href"] == second_blob["href"]
    assert first_blob["href"].startswith("blobs/sha256/")
    assert (output_root / first_blob["href"]).read_text() == "same"
    assert len(_blob_files(output_root)) == 1
    assert not (bundle_path / "artifacts").exists()


def test_export_resolves_code_state_before_batch_evaluate(
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
        if target == "base_width * 2":
            assert definition_overrides == {"base_width": 500}
            assert object_patches is None
            return {
                "target": target,
                "results": [{"value": 1000}],
                "metadata": {"batch": {"result_count": 1}},
            }

        assert definition_overrides == [{"base_width": 500, "chart_width": 1000}]
        assert object_patches == [{}]
        return {
            "target": target,
            "results": [{"value": {"summary": "ok"}}],
            "metadata": {"batch": {"result_count": 1}},
        }

    monkeypatch.setattr(export_module, "evaluate", fake_evaluate)
    monkeypatch.setattr(
        request_module,
        "get_context",
        lambda: SimpleNamespace(filename=str(notebook)),
    )

    result = run(
        export_module.export(
            {
                "bundle": str(tmp_path / "bundle"),
                "scenarios": [
                    {
                        "id": "computed",
                        "inputs": {
                            "base_width": 500,
                            "chart_width": {
                                "type": "code",
                                "expression": "base_width * 2",
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
        "format": "summary.v1",
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
            }
        )
    )

    manifest = result.manifest
    artifact = _artifact(manifest, "computed", "summary", "json")
    blob = artifact["data"]["files"]["data"]
    output_root = Path(result.bundle_path).parent.parent
    blob_value = json.loads((output_root / blob["href"]).read_text())
    assert blob_value == "ok"
    scenario = _scenario(manifest, "computed")
    assert scenario["state"]["inputs"]["chart_width"] == 1000
    assert scenario["declared_state"]["inputs"]["chart_width"] == {
        "type": "code",
        "expression": "base_width * 2",
    }


def test_export_rejects_embedded_artifacts(
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

    monkeypatch.setattr(export_module, "evaluate", fake_evaluate)
    monkeypatch.setattr(
        request_module,
        "get_context",
        lambda: SimpleNamespace(filename=str(notebook)),
    )

    with pytest.raises(TypeError, match="type='bundle'"):
        run(
            export_module.export(
                {
                    "bundle": str(tmp_path / "bundle"),
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
        "format": "summary.v1",
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
                }
            )
        )
    assert not list((tmp_path / "bundle").glob(".tmp-*"))


def test_export_default_bundle_path_uses_marimo_output_dir(
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

    monkeypatch.setattr(export_module, "evaluate", fake_evaluate)
    monkeypatch.setattr(
        request_module,
        "get_context",
        lambda: SimpleNamespace(filename=str(notebook)),
    )

    result = run(
        export_module.export(
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
    assert not (bundle_path / "notebook.py").exists()
    assert not (bundle_path / "artifacts").exists()
    assert Path(result.manifest_path).exists()


def test_export_uses_live_notebook_path_over_spec_notebook(
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

    monkeypatch.setattr(export_module, "evaluate", fake_evaluate)
    monkeypatch.setattr(
        request_module,
        "get_context",
        lambda: SimpleNamespace(filename=str(live_notebook)),
    )

    result = run(
        export_module.export(
            {
                "notebook": "stale.py",
                "bundle": str(tmp_path / "bundle"),
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

    _assert_notebook_source(
        Path(result.bundle_path).parent.parent,
        result.manifest,
        name="live.py",
    )


def test_export_default_bundle_path_groups_same_scenario_set(
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

    monkeypatch.setattr(export_module, "evaluate", fake_evaluate)
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

    first = run(export_module.export(title_spec))
    second = run(export_module.export(title_and_summary_spec))

    first_path = Path(first.bundle_path)
    second_path = Path(second.bundle_path)
    output_root = first_path.parent.parent
    assert output_root == second_path.parent.parent
    assert first_path != second_path
    assert first.manifest["scenario_set"] == second.manifest["scenario_set"]
    assert first.manifest["notebook"] == second.manifest["notebook"]
    assert not (first_path / "notebook.py").exists()
    assert not (second_path / "notebook.py").exists()
    assert (
        first.manifest["capture"]["request_sha256"]
        != second.manifest["capture"]["request_sha256"]
    )
    assert len(_blob_files(output_root)) == 1


def test_export_default_bundle_path_shares_blobs_across_renamed_values(
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

    monkeypatch.setattr(export_module, "evaluate", fake_evaluate)
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

    first = run(export_module.export(spec("title")))
    second = run(export_module.export(spec("renamed_title")))

    first_path = Path(first.bundle_path)
    second_path = Path(second.bundle_path)
    output_root = first_path.parent.parent

    assert output_root == second_path.parent.parent
    assert first_path != second_path
    assert first.manifest["id"] != second.manifest["id"]
    assert len(_blob_files(output_root)) == 1

    first_artifact = _artifact(first.manifest, "default", "title", "text")
    second_artifact = _artifact(second.manifest, "default", "renamed_title", "text")
    assert (
        first_artifact["data"]["files"]["value"]["href"]
        == second_artifact["data"]["files"]["value"]["href"]
    )
    href = first_artifact["data"]["files"]["value"]["href"]
    assert (output_root / href).read_text() == "same"
    assert first_artifact["data"]["files"]["value"]["href"].startswith("blobs/sha256/")


def test_export_records_trace_without_changing_bundle_identity(
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

    monkeypatch.setattr(export_module, "evaluate", fake_evaluate)
    monkeypatch.setattr(
        request_module,
        "get_context",
        lambda: SimpleNamespace(filename=str(notebook)),
    )

    spec = {
        "bundle": str(tmp_path / "bundle"),
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

    first = run(export_module.export(spec))
    second = run(export_module.export(spec))

    assert first.bundle_path == second.bundle_path
    assert first.manifest["id"] == second.manifest["id"]
    assert first.invocation_path != second.invocation_path
    assert "trace" not in _scenario(second.manifest, "default")
    assert _invocation_scenario(second.invocation, "default")["trace"]["graph"][
        "nodes"
    ] == [{"cell_id": "cell", "status": "run-2"}]
    invocation_index = json.loads(Path(second.invocation_index_path).read_text())
    assert [item["id"] for item in invocation_index["invocations"]] == [
        first.invocation["id"],
        second.invocation["id"],
    ]


def test_export_default_bundle_path_separates_different_scenario_sets(
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

    monkeypatch.setattr(export_module, "evaluate", fake_evaluate)
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
            {"id": "wide", "inputs": {"chart_width": 1200}},
        ],
    }

    first = run(export_module.export(base_spec))
    second = run(export_module.export(wide_spec))

    first_path = Path(first.bundle_path)
    second_path = Path(second.bundle_path)
    assert first_path.parent.parent == second_path.parent.parent
    assert first_path != second_path
    assert len(_blob_files(first_path.parent.parent)) == 1
    assert first.manifest["scenario_set"] != second.manifest["scenario_set"]
    assert first.manifest["notebook"] == second.manifest["notebook"]


def test_export_scenario_set_grouping_is_order_insensitive(
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

    monkeypatch.setattr(export_module, "evaluate", fake_evaluate)
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
        export_module.export(
            {
                "scenarios": [
                    {"id": "wide", "inputs": {"chart_width": 1200}},
                    {"id": "default"},
                ],
                "values": values,
            }
        )
    )
    second = run(
        export_module.export(
            {
                "scenarios": [
                    {"id": "default"},
                    {"id": "wide", "inputs": {"chart_width": 1200}},
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
