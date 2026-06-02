from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from capture_test_helpers import (
    assert_notebook_source,
    export_module,
    install_test_exporters,
    request_module,
    run,
)


def test_capture_default_bundle_path_uses_marimo_output_dir(
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
    assert_notebook_source(bundle_path.parent.parent, result.manifest)
    assert Path(result.manifest_path).exists()
    assert Path(result.invocation_index_path).exists()
    assert Path(result.invocation_path).exists()


def test_capture_uses_running_notebook_path_over_spec_notebook(
    tmp_path: Path,
    monkeypatch,
) -> None:
    install_test_exporters()
    running_notebook = tmp_path / "running.py"
    running_notebook.write_text("# notebook")

    async def fake_evaluate(
        target: str,
        resolved_scenarios: list[dict[str, Any]],
        **_runtime_options: Any,
    ):
        del target, resolved_scenarios
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
        lambda: SimpleNamespace(filename=str(running_notebook)),
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

    assert_notebook_source(
        Path(result.bundle_path).parent.parent,
        result.manifest,
        name="running.py",
    )
