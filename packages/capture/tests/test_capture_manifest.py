from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from moexport.bundle.schema import BundleManifest

from capture_test_helpers import (
    assert_notebook_source,
    blob_files,
    format_record,
    install_test_exporters,
    invocation_scenario,
    package_capture,
    request_module,
    run,
    export_module,
)


def test_capture_writes_manifest_formats_and_deduped_blobs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    install_test_exporters()
    notebook = tmp_path / "finance.py"
    notebook.write_text("# notebook")

    async def fake_evaluate(
        target: str,
        resolved_scenarios: list[dict[str, Any]],
        **_runtime_options: Any,
    ):
        del target, resolved_scenarios
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
    first_format = format_record(manifest, "default", "title", "text")
    second_format = format_record(manifest, "wide-chart", "title", "text")
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
    assert_notebook_source(output_root, manifest)
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
    assert invocation_scenario(invocation, "default")["trace"]["graph"]["nodes"] == [
        {"cell_id": "a", "status": "inactive"}
    ]
    assert invocation_scenario(invocation, "wide-chart")["trace"]["execution"][
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
    assert len(blob_files(output_root)) == 1


def test_capture_rejects_embedded_format_payloads(
    tmp_path: Path,
    monkeypatch,
) -> None:
    notebook = tmp_path / "finance.py"
    notebook.write_text("# notebook")

    async def fake_evaluate(
        target: str,
        resolved_scenarios: list[dict[str, Any]],
        **_runtime_options: Any,
    ):
        del target, resolved_scenarios
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
