from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from capture_test_helpers import (
    blob_files,
    export_module,
    format_record,
    install_test_exporters,
    request_module,
    run,
)


def test_capture_default_bundle_path_groups_samescenario_set(
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
        del target
        return {
            "target": "target",
            "results": [
                {"value": {"title": "same", "summary": "same"}}
                for _ in resolved_scenarios
            ],
            "metadata": {
                "batch": {
                    "result_count": len(resolved_scenarios),
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
    assert len(blob_files(output_root)) == 1


def test_capture_default_bundle_path_shares_blobs_across_renamed_values(
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
        del resolved_scenarios
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
    assert len(blob_files(output_root)) == 1

    first_format = format_record(first.manifest, "default", "title", "text")
    second_format = format_record(second.manifest, "default", "renamed_title", "text")
    assert (
        first_format["data"]["files"]["value"]["href"]
        == second_format["data"]["files"]["value"]["href"]
    )
    href = first_format["data"]["files"]["value"]["href"]
    assert (output_root / href).read_text() == "same"
    assert first_format["data"]["files"]["value"]["href"].startswith("blobs/sha256/")


def test_capture_default_bundle_path_separates_differentscenario_sets(
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
        del target
        return {
            "target": "target",
            "results": [{"value": {"title": "same"}} for _ in resolved_scenarios],
            "metadata": {
                "batch": {
                    "result_count": len(resolved_scenarios),
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
    assert len(blob_files(first_path.parent.parent)) == 1
    assert first.manifest["scenario_set"] != second.manifest["scenario_set"]
    assert first.manifest["notebook"] == second.manifest["notebook"]


def test_capturescenario_set_grouping_is_order_insensitive(
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
        del target
        return {
            "target": "target",
            "results": [
                {"value": {"title": str(index)}}
                for index, _ in enumerate(resolved_scenarios)
            ],
            "metadata": {
                "batch": {
                    "result_count": len(resolved_scenarios),
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
