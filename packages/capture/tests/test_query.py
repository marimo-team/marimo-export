from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from moexport.jsonio import write_json
from moexport.query import open_export


def _notebook_record(sha256: str, *, name: str = "finance.py") -> dict[str, object]:
    return {
        "name": name,
        "source_sha256": sha256,
        "source": {
            "href": f"blobs/sha256/no/te/{sha256}",
            "media_type": "text/x-python",
            "size": 123,
            "sha256": sha256,
        },
    }


def _definition_source(name: str) -> dict[str, str]:
    return {"type": "definition", "name": name}


def _write_notebook_source(root: Path, sha256: str, text: str) -> None:
    source = cast(dict[str, object], _notebook_record(sha256)["source"])
    href = source["href"]
    assert isinstance(href, str)
    path = root / href
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_export(root: Path) -> None:
    blob = root / "blobs" / "sha256" / "aa" / "bb" / "aabb"
    blob.parent.mkdir(parents=True)
    blob.write_text('{"hello": "world"}', encoding="utf-8")
    _write_notebook_source(root, "notebook-sha", "print('finance')\n")

    bundle = root / "bundles" / "sha256-demo"
    manifest = {
        "schema": "moexport.bundle.v1",
        "version": 1,
        "id": "sha256-demo",
        "sha256": "demo",
        "notebook": _notebook_record("notebook-sha"),
        "scenario_set": {"id": "sha256-scenarios", "sha256": "scenarios"},
        "capture": {
            "id": "sha256-export",
            "request_sha256": "export",
        },
        "values": {
            "prices": {"source": _definition_source("df"), "artifacts": ["json"]},
        },
        "scenarios": [
            {
                "id": "base",
                "state": {},
                "values": {
                    "prices": {
                        "json": {
                            "format_id": "example.json.v1",
                            "media_type": "application/json",
                            "data": {
                                "type": "bundle",
                                "files": {
                                    "data": {
                                        "href": "blobs/sha256/aa/bb/aabb",
                                        "media_type": "application/json",
                                        "size": 18,
                                        "sha256": "aabb",
                                    }
                                },
                                "entry": "data",
                            },
                            "metadata": {"rows": 1},
                        }
                    }
                },
            }
        ],
        "provenance": {
            "invocations_index_href": "bundles/sha256-demo/traces/index.json",
        },
    }
    write_json(bundle / "manifest.json", manifest)

    trace = {
        "schema": "moexport.invocation.v1",
        "version": 1,
        "id": "sha256-trace",
        "sha256": "trace-sha",
        "created_at": "2026-05-02T00:00:00Z",
        "bundle": {
            "id": "sha256-demo",
            "sha256": "demo",
            "manifest_href": "bundles/sha256-demo/manifest.json",
        },
        "notebook": _notebook_record("notebook-sha"),
        "scenario_set": {"id": "sha256-scenarios", "sha256": "scenarios"},
        "capture": {
            "id": "sha256-export",
            "request_sha256": "export",
        },
        "source_spec": {"sha256": "spec-sha", "spec": {}},
        "scenarios": [
            {
                "id": "base",
                "state": {},
                "trace": {
                    "value_preview": "{'prices': ...}",
                    "graph": {
                        "nodes": [{"cell_id": "abc", "defs": ["df"]}],
                        "edges": [],
                        "counts": {"nodes": 1, "edges": 0},
                    },
                    "execution": {"stats": {"executed": 1}},
                },
            }
        ],
        "evaluation": {"batch": {"result_count": 1}},
    }
    write_json(bundle / "traces" / "sha256-trace.json", trace)
    write_json(
        bundle / "traces" / "index.json",
        {
            "schema": "moexport.invocation_index.v1",
            "version": 1,
            "bundle": {
                "id": "sha256-demo",
                "sha256": "demo",
                "manifest_href": "bundles/sha256-demo/manifest.json",
            },
            "invocations": [
                {
                    "id": "sha256-trace",
                    "sha256": "trace-sha",
                    "created_at": "2026-05-02T00:00:00Z",
                    "href": "bundles/sha256-demo/traces/sha256-trace.json",
                }
            ],
        },
    )


def _write_chart_export(root: Path, *, bundle_id: str, chart_width: int) -> None:
    href = f"blobs/sha256/{chart_width}/spec"
    blob = root / href
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_text('{"mark": "line"}', encoding="utf-8")
    _write_notebook_source(root, f"notebook-{bundle_id}", f"# {bundle_id}\n")

    bundle = root / "bundles" / bundle_id
    write_json(
        bundle / "manifest.json",
        {
            "schema": "moexport.bundle.v1",
            "version": 1,
            "id": bundle_id,
            "sha256": bundle_id,
            "notebook": _notebook_record(f"notebook-{bundle_id}"),
            "scenario_set": {"id": f"{bundle_id}-scenarios", "sha256": "scenarios"},
            "capture": {
                "id": f"{bundle_id}-export",
                "request_sha256": "export",
            },
            "values": {
                "comparison_chart": {
                    "source": _definition_source("symbols_chart"),
                    "artifacts": ["vegalite"],
                },
            },
            "scenarios": [
                {
                    "id": "wide_chart",
                    "state": {"chart_width": chart_width},
                    "values": {
                        "comparison_chart": {
                            "vegalite": {
                                "format_id": "vegalite.v1",
                                "media_type": "application/vnd.vegalite+json",
                                "data": {
                                    "type": "bundle",
                                    "files": {
                                        "spec": {
                                            "href": href,
                                            "media_type": "application/vnd.vegalite+json",
                                            "size": 16,
                                            "sha256": f"spec-{chart_width}",
                                        }
                                    },
                                    "entry": "spec",
                                },
                                "metadata": {"schema": "vega-lite"},
                            }
                        }
                    },
                }
            ],
            "provenance": {},
        },
    )


def test_open_export_lists_and_opens_bundles(tmp_path: Path) -> None:
    root = tmp_path / "__marimo__" / "static-export"
    _write_export(root)

    export = open_export(root)

    assert export.bundles() == [
        {
            "id": "sha256-demo",
            "sha256": "demo",
            "path": str(root / "bundles" / "sha256-demo"),
            "manifest_path": str(root / "bundles" / "sha256-demo" / "manifest.json"),
            "notebook": _notebook_record("notebook-sha"),
            "capture": {
                "id": "sha256-export",
                "request_sha256": "export",
            },
            "value_count": 1,
            "scenario_count": 1,
            "values": ["prices"],
            "scenarios": ["base"],
        }
    ]
    assert export.bundle("sha256-dem").id == "sha256-demo"
    assert open_export(root / "bundles" / "sha256-demo").bundle().id == "sha256-demo"
    assert open_export(
        root / "bundles" / "sha256-demo" / "manifest.json"
    ).bundle().id == ("sha256-demo")


def test_bundle_query_returns_semantic_map_and_raw_file_paths(tmp_path: Path) -> None:
    root = tmp_path / "__marimo__" / "static-export"
    _write_export(root)
    bundle = open_export(root).bundle()

    artifact = bundle.artifacts(scenario="base", value="prices", artifact="json")[0]
    assert bundle.artifact(scenario="base", value="prices", artifact="json") == artifact
    files = bundle.files(dedupe=True)

    assert artifact["source"] == _definition_source("df")
    assert artifact["format_id"] == "example.json.v1"
    assert artifact["entry_path"] == str(
        root / "blobs" / "sha256" / "aa" / "bb" / "aabb"
    )
    assert files == [
        {
            "href": "blobs/sha256/aa/bb/aabb",
            "media_type": "application/json",
            "size": 18,
            "sha256": "aabb",
            "path": str(root / "blobs" / "sha256" / "aa" / "bb" / "aabb"),
            "exists": True,
            "uses": [
                {
                    "scenario": "base",
                    "state": {},
                    "value": "prices",
                    "source": _definition_source("df"),
                    "artifact": "json",
                    "format_id": "example.json.v1",
                    "file": "data",
                }
            ],
        }
    ]
    assert bundle.resolve("blobs/sha256/aa/bb/aabb") == (
        root / "blobs" / "sha256" / "aa" / "bb" / "aabb"
    )
    with pytest.raises(ValueError, match="invalid bundle href"):
        bundle.resolve("../secret.json")
    with pytest.raises(ValueError, match="invalid bundle href"):
        bundle.resolve("/tmp/secret.json")
    assert bundle.map()["files"] == files
    assert bundle.file(scenario="base", value="prices", artifact="json")["path"] == str(
        root / "blobs" / "sha256" / "aa" / "bb" / "aabb"
    )
    assert bundle.entry(
        scenario="base",
        value="prices",
        artifact="json",
        include_content=True,
    ) == {
        "bundle": "sha256-demo",
        "scenario": "base",
        "state": {},
        "value": "prices",
        "source": _definition_source("df"),
        "artifact": "json",
        "format_id": "example.json.v1",
        "artifact_media_type": "application/json",
        "metadata": {"rows": 1},
        "entry": "data",
        "href": "blobs/sha256/aa/bb/aabb",
        "media_type": "application/json",
        "size": 18,
        "sha256": "aabb",
        "path": str(root / "blobs" / "sha256" / "aa" / "bb" / "aabb"),
        "exists": True,
        "content": {"type": "json", "value": {"hello": "world"}},
    }


def test_bundle_query_exposes_traces_and_graphs(tmp_path: Path) -> None:
    root = tmp_path / "__marimo__" / "static-export"
    _write_export(root)
    bundle = open_export(root).bundle()

    assert bundle.traces()[0]["path"] == str(
        root / "bundles" / "sha256-demo" / "traces" / "sha256-trace.json"
    )
    scenario_trace = bundle.trace("base")
    assert scenario_trace["id"] == "base"
    assert scenario_trace["state"] == {}
    assert scenario_trace["trace"]["value_preview"] == "{'prices': ...}"
    assert bundle.trace()["scenarios"][0]["id"] == "base"
    assert bundle.graph("base") == {
        "nodes": [{"cell_id": "abc", "defs": ["df"]}],
        "edges": [],
        "counts": {"nodes": 1, "edges": 0},
    }
    assert bundle.graph()["base"]["counts"] == {"nodes": 1, "edges": 0}


def test_bundle_query_rejects_manifest_hrefs_outside_export_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "__marimo__" / "static-export"
    _write_export(root)
    manifest_path = root / "bundles" / "sha256-demo" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["scenarios"][0]["values"]["prices"]["json"]["data"]["files"]["data"][
        "href"
    ] = "../secret.json"
    write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match="invalid bundle href"):
        open_export(root).bundle()


def test_bundle_query_reports_invalid_json_entry_content(tmp_path: Path) -> None:
    root = tmp_path / "__marimo__" / "static-export"
    _write_export(root)
    (root / "blobs" / "sha256" / "aa" / "bb" / "aabb").write_text(
        "{not json",
        encoding="utf-8",
    )

    entry = (
        open_export(root)
        .bundle()
        .entry(
            scenario="base",
            value="prices",
            artifact="json",
            include_content=True,
        )
    )

    assert entry["content"]["type"] == "invalid-json"
    assert entry["content"]["text"] == "{not json"


def test_export_query_returns_notebook_source_for_matching_scenario(
    tmp_path: Path,
) -> None:
    root = tmp_path / "__marimo__" / "static-export"
    _write_chart_export(root, bundle_id="sha256-1000", chart_width=1000)
    _write_chart_export(root, bundle_id="sha256-1200", chart_width=1200)

    export = open_export(root)
    source = export.notebook_source(state={"chart_width": 1200})

    assert source["name"] == "finance.py"
    assert source["text"] == "# sha256-1200\n"
    assert source["exists"] is True
    assert source["bundles"] == ["sha256-1200"]
    assert source["scenarios"] == ["wide_chart"]
    source_blob = cast(dict[str, object], source["source"])
    assert source_blob["sha256"] == "notebook-sha256-1200"

    with pytest.raises(ValueError, match="multiple notebook sources"):
        export.notebook_source()


def test_export_query_reports_ambiguous_bundle_selection(tmp_path: Path) -> None:
    root = tmp_path / "__marimo__" / "static-export"
    _write_export(root)
    _write_export(root.parent / "other")
    other_manifest = root.parent / "other" / "bundles" / "sha256-demo" / "manifest.json"
    other_bundle = root / "bundles" / "sha256-demo-2"
    other_bundle.mkdir(parents=True)
    other_manifest.replace(other_bundle / "manifest.json")

    with pytest.raises(ValueError, match="multiple bundles"):
        open_export(root).bundle()


def test_export_query_catalog_indexes_the_whole_export_root(tmp_path: Path) -> None:
    root = tmp_path / "__marimo__" / "static-export"
    _write_chart_export(root, bundle_id="sha256-1000", chart_width=1000)
    _write_chart_export(root, bundle_id="sha256-1200", chart_width=1200)

    catalog = open_export(root).catalog()

    assert catalog["counts"] == {
        "bundles": 2,
        "notebooks": 2,
        "scenarios": 2,
        "values": 1,
        "artifacts": 2,
        "files": 2,
        "bytes": 32,
    }
    assert catalog["state_keys"] == ["chart_width"]
    assert catalog["values"] == [
        {
            "name": "comparison_chart",
            "sources": [_definition_source("symbols_chart")],
            "artifacts": ["vegalite"],
            "bundles": ["sha256-1000", "sha256-1200"],
        }
    ]
    assert catalog["notebooks"] == [
        {
            **_notebook_record("notebook-sha256-1000"),
            "bundles": ["sha256-1000"],
            "captures": ["sha256-1000-export"],
            "values": ["comparison_chart"],
            "scenario_count": 1,
        },
        {
            **_notebook_record("notebook-sha256-1200"),
            "bundles": ["sha256-1200"],
            "captures": ["sha256-1200-export"],
            "values": ["comparison_chart"],
            "scenario_count": 1,
        },
    ]


def test_export_query_filters_1200_wide_finance_chart(tmp_path: Path) -> None:
    root = tmp_path / "__marimo__" / "static-export"
    _write_chart_export(root, bundle_id="sha256-1000", chart_width=1000)
    _write_chart_export(root, bundle_id="sha256-1200", chart_width=1200)

    export = open_export(root)
    artifacts = export.artifacts(
        state={"chart_width": 1200},
        value="comparison_chart",
        artifact="vegalite",
    )
    files = export.files(
        state={"chart_width": 1200},
        value="comparison_chart",
        media_type="application/vnd.vegalite+json",
    )
    entries = export.entries(
        state={"chart_width": 1200},
        value="comparison_chart",
        media_type="application/vnd.vegalite+json",
        include_content=True,
    )
    scenarios = export.scenarios(state={"chart_width": 1200})

    assert scenarios == [
        {
            "bundle": "sha256-1200",
            "bundle_path": str(root / "bundles" / "sha256-1200"),
            "notebook": _notebook_record("notebook-sha256-1200"),
            "id": "wide_chart",
            "state": {"chart_width": 1200},
            "values": {"comparison_chart": ["vegalite"]},
            "artifact_count": 1,
        }
    ]
    assert (
        export.artifact(
            state={"chart_width": 1200},
            value="comparison_chart",
            artifact="vegalite",
        )
        == artifacts[0]
    )
    assert (
        export.file(
            state={"chart_width": 1200},
            value="comparison_chart",
            media_type="application/vnd.vegalite+json",
        )
        == files[0]
    )
    assert (
        export.entry(
            state={"chart_width": 1200},
            value="comparison_chart",
            media_type="application/vnd.vegalite+json",
            include_content=True,
        )
        == entries[0]
    )
    assert artifacts[0]["bundle"] == "sha256-1200"
    assert artifacts[0]["scenario"] == "wide_chart"
    assert artifacts[0]["state"] == {"chart_width": 1200}
    assert artifacts[0]["entry_path"] == str(
        root / "blobs" / "sha256" / "1200" / "spec"
    )

    assert files[0]["path"] == str(root / "blobs" / "sha256" / "1200" / "spec")
    assert files[0]["uses"][0]["bundle"] == "sha256-1200"
    assert entries[0]["content"] == {"type": "json", "value": {"mark": "line"}}
