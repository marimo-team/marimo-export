from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from moexport.bundle.schema import BundleManifest

from capture_test_helpers import (
    export_module,
    install_test_exporters,
    invocation_scenario,
    request_module,
    run,
)


def test_capture_records_trace_without_changing_bundle_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    install_test_exporters()
    notebook = tmp_path / "finance.py"
    notebook.write_text("# notebook")
    run_count = 0

    async def fake_evaluate(
        target: str,
        resolved_scenarios: list[dict[str, Any]],
        **_runtime_options: Any,
    ):
        nonlocal run_count
        run_count += 1
        del target, resolved_scenarios
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
    assert invocation_scenario(second.invocation, "default")["trace"]["graph"][
        "nodes"
    ] == [{"cell_id": "cell", "status": "run-2"}]
    invocation_index = json.loads(Path(second.invocation_index_path).read_text())
    assert [item["id"] for item in invocation_index["invocations"]] == [
        first.invocation["id"],
        second.invocation["id"],
    ]
