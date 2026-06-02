from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from marimo._ast.compiler import compile_cell
from marimo._runtime.dataflow import DirectedGraph
from marimo._types.ids import CellId_t

from capture_test_helpers import (
    export_module,
    format_record,
    install_test_exporters,
    invocation_scenario,
    request_module,
    run,
    scenario,
    target_module,
)


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
    artifact = format_record(manifest, "computed", "summary", "json")
    blob = artifact["data"]["files"]["data"]
    output_root = Path(result.bundle_path).parent.parent
    blob_value = json.loads((output_root / blob["href"]).read_text())
    assert blob_value == "500x1000"
    scenario_record = scenario(manifest, "computed")
    assert scenario_record["state"]["chart_width"] == 1000
    assert scenario_record["declared_state"]["chart_width"] == {
        "code": "base_width * 2",
    }


def test_capture_applies_dotted_state_keys_to_object_attributes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    install_test_exporters()
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
    artifact = format_record(result.manifest, "patched", "title", "text")
    blob = artifact["data"]["files"][artifact["data"]["entry"]]
    output_root = Path(result.bundle_path).parent.parent
    assert (output_root / blob["href"]).read_text() == "Symbols: AAPL"
    assert invocation_scenario(result.invocation, "patched")["trace"]["state"][
        "applied_state_updates"
    ] == [
        {
            "target": "selector.config",
            "root": "selector",
            "value_preview": "{'label': 'Symbols'}",
        }
    ]


def test_capture_rejects_non_jsonscenario_state_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    notebook = tmp_path / "finance.py"
    notebook.write_text("# notebook")

    async def fake_evaluate(
        target: str,
        resolved_scenarios: Any = None,
        **_runtime_options: Any,
    ):
        del resolved_scenarios
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
