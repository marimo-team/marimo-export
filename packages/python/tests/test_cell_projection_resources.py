from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from export_integration_support import build
from marimo_export import ExportSpec, OutputSpec, open_export
from marimo_export._json import decode_json_object
from marimo_export._marimo.compat.replay import _resolve_ui_object_id
from marimo_export.errors import ExecutionError, OutputError


def test_complete_cell_resolves_one_external_scratch_ui_alias() -> None:
    external = UUID("12345678-1234-4234-8234-123456789abc")
    scoped = f"{external}PKri-0"
    registry = SimpleNamespace(
        _objects={scoped: object()},
        _constructing_cells={scoped: f"{external}PKri"},
    )

    assert _resolve_ui_object_id(registry, "__scratch__-0", "PKri") == scoped


def test_complete_cell_rejects_missing_or_ambiguous_scratch_ui_aliases() -> None:
    first = UUID("12345678-1234-4234-8234-123456789abc")
    second = UUID("87654321-4321-4321-8321-cba987654321")
    candidates = [f"{scope}PKri-0" for scope in (first, second)]
    registry = SimpleNamespace(
        _objects={candidate: object() for candidate in candidates},
        _constructing_cells={candidate: candidate.removesuffix("-0") for candidate in candidates},
    )

    with pytest.raises(KeyError):
        _resolve_ui_object_id(SimpleNamespace(_objects={}), "__scratch__-0", "PKri")
    with pytest.raises(KeyError):
        _resolve_ui_object_id(registry, "__scratch__-0", "PKri")


def test_output_and_cell_snapshots_scope_shared_widget_and_control_graphs(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def controls():
    import anywidget
    import marimo as mo
    import traitlets

    class Counter(anywidget.AnyWidget):
        _esm = "export default { render() {} }"
        value = traitlets.Int(1).tag(sync=True)

    scale = mo.ui.slider(1, 5, value=2)
    widget = mo.ui.anywidget(Counter())
    mo.hstack([widget, scale])
    return scale, widget


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    result = build(
        notebook,
        spec=ExportSpec(
            default_state="selected",
            states={"selected": {"scale": 3}},
            outputs={
                "widget": OutputSpec.output("widget"),
                "scale": OutputSpec.output("scale"),
                "controls": OutputSpec.cell("controls"),
            },
        ),
        output=tmp_path / "export",
        timeout=30,
    )
    notebook_export = open_export(result.path)
    state = notebook_export.state("selected")

    snapshots = {
        name: cast(
            dict[str, Any],
            decode_json_object(state.output(name).asset_bytes(), f"{name} snapshot"),
        )
        for name in ("widget", "scale", "controls")
    }
    resource_sets = {
        name: set(snapshot["resources"]["uiValues"]) for name, snapshot in snapshots.items()
    }

    assert resource_sets["widget"].isdisjoint(resource_sets["controls"])
    assert resource_sets["scale"].isdisjoint(resource_sets["controls"])
    for name, snapshot in snapshots.items():
        resources = snapshot["resources"]
        owner_cell_id = snapshot["ownerCellId"] if name != "controls" else snapshot["cell"]["id"]
        assert all(
            object_id.startswith(f"{owner_cell_id}-projection-")
            for object_id in resource_sets[name]
        )
        assert set(resources["functions"]) == resource_sets[name]
        assert all(object_id in snapshot["output"]["data"] for object_id in resource_sets[name])
        assert f'random-id="{owner_cell_id}-projection-' in snapshot["output"]["data"]
    for name in ("widget", "controls"):
        snapshot = snapshots[name]
        root_model_id = snapshot["resources"]["modelNotifications"][0]["model_id"]
        assert snapshot["output"]["data"].count(root_model_id) >= 2

    control_bindings = dict(notebook_export.control_bindings)
    projected_input_ids = {
        "scale": {
            object_id
            for snapshot in snapshots.values()
            for object_id, value in snapshot["resources"]["uiValues"].items()
            if value == 3
        },
        "widget": {
            object_id
            for snapshot in snapshots.values()
            for object_id, value in snapshot["resources"]["uiValues"].items()
            if isinstance(value, dict) and "model_id" in value
        },
    }
    assert len(control_bindings) == 4
    assert {binding.input for binding in control_bindings.values()} == {"scale", "widget"}
    assert {binding.path for binding in control_bindings.values()} == {()}
    assert set(control_bindings) == projected_input_ids["scale"] | projected_input_ids["widget"]
    for input_name, object_ids in projected_input_ids.items():
        assert {
            object_id
            for object_id, binding in control_bindings.items()
            if binding.input == input_name
        } == object_ids


def test_complete_cell_rejects_sensitive_ui_replay_values(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def secret_cell():
    import marimo as mo
    secret = mo.ui.text(kind="password", value="private")
    secret
    return (secret,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(OutputError) as raised:
        build(
            notebook,
            spec=ExportSpec(
                default_state="baseline",
                states={"baseline": {}},
                outputs={"secret": OutputSpec.cell("secret_cell")},
            ),
            output=tmp_path / "export",
            timeout=30,
        )

    assert raised.value.code == "output_execution_failed"
    assert {key: value for key, value in raised.value.details.items() if key != "cell_id"} == {
        "output": "secret",
        "selector": "secret_cell",
        "selector_by": "name",
        "source_kind": "cell",
        "state": "baseline",
    }
    assert isinstance(raised.value.details["cell_id"], str)
    assert not (tmp_path / "export").exists()


def test_state_rejects_conditionally_revealed_sensitive_input_tree(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def controls():
    import marimo as mo
    reveal = mo.ui.slider(0, 1, value=0)
    return mo, reveal


@app.cell
def filters_control(mo, reveal):
    fields = {"label": mo.ui.text(value="public")}
    if reveal.value == 1:
        fields["credential"] = mo.ui.text(kind="password", value="classified-value")
    filters = mo.ui.dictionary(fields).form()
    filters
    return (filters,)


@app.cell
def report(filters):
    metric = "ready" if filters.value is None else "submitted"
    return (metric,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    specs = (
        ExportSpec(
            default_state="revealed",
            states={"revealed": {"reveal": 1}},
            outputs={"value": OutputSpec.json("metric")},
        ),
        ExportSpec(
            default_state="revealed",
            states={"revealed": {"reveal": 1}},
            outputs={
                "value": OutputSpec.json("metric"),
                "output": OutputSpec.output("filters"),
                "cell": OutputSpec.cell("filters_control"),
            },
        ),
    )

    for index, spec in enumerate(specs):
        output = tmp_path / f"export-{index}"
        with pytest.raises(ExecutionError) as raised:
            build(notebook, spec=spec, output=output, timeout=30)
        assert raised.value.code == "input_value_invalid"
        assert raised.value.details == {"state": "revealed", "input": "filters"}
        assert "classified-value" not in str(raised.value)
        assert "classified-value" not in repr(raised.value.details)
        assert not output.exists()
