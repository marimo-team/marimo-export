from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from export_integration_support import build
from export_integration_support import capture_session as _capture_session
from marimo_export import ExportSpec, OutputSpec, open_export
from marimo_export._json import decode_json_object
from marimo_export.index import ControlIndexStep
from marimo_export.producer import open_notebook


def test_complete_ui_cell_replays_the_selected_value_across_warm_captures(
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
    scale = mo.ui.slider(1, 5, value=2)
    scale
    return (scale,)


@app.cell
def _(scale):
    metric = scale.value * 21
    return (metric,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    spec = ExportSpec(
        default_state="selected",
        states={"selected": {"scale": 3}},
        outputs={
            "controls": OutputSpec.cell("controls"),
            "metric": OutputSpec.json("metric"),
        },
    )

    first = build(notebook, spec=spec, output=tmp_path / "first", timeout=30)
    second = build(notebook, spec=spec, output=tmp_path / "second", timeout=30)

    for result in (first, second):
        state = open_export(result.path).state("selected")
        snapshot = cast(
            dict[str, Any],
            decode_json_object(state.output("controls").asset_bytes(), "cell snapshot"),
        )
        assert state.inputs == {"scale": 3}
        assert state.output("metric").json() == 63
        assert list(snapshot["resources"]["uiValues"].values()) == [3]
        assert set(snapshot["resources"]["uiValues"]) == set(snapshot["resources"]["functions"])
        object_id = next(iter(snapshot["resources"]["uiValues"]))
        assert object_id.startswith(f"{snapshot['cell']['id']}-projection-")
        assert f"-ui-{snapshot['cell']['id']}-" in object_id
    assert (
        second.cache_activity.authored_hits,
        second.cache_activity.authored_misses,
    ) == (1, 1)


def test_nondefault_ui_state_executes_projected_cells_once_in_final_phase(
    tmp_path: Path,
) -> None:
    counter = tmp_path / "runs.txt"
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        f"""
import marimo

app = marimo.App()


@app.cell
def controls():
    import marimo as mo
    scale = mo.ui.slider(1, 5, value=1)
    return (scale,)


@app.cell
def report(scale):
    from pathlib import Path
    path = Path({str(counter)!r})
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"{{scale.value}}\\n")
    summary = f"scale={{scale.value}}"
    marker = lambda: None
    print(summary)
    summary
    return marker, summary


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    spec = ExportSpec(
        default_state="selected",
        states={"selected": {"scale": 3}},
        outputs={
            "value": OutputSpec.json("summary"),
            "output": OutputSpec.output("summary"),
            "cell": OutputSpec.cell("report"),
        },
    )

    with open_notebook(notebook, timeout=30) as producer:
        session = producer._require_open()
        counter.write_text("", encoding="utf-8")
        _capture_session(session, spec, tmp_path / "first")
        counter.write_text("", encoding="utf-8")
        _capture_session(session, spec, tmp_path / "second")

    assert counter.read_text(encoding="utf-8") == "3\n"
    for output in (tmp_path / "first", tmp_path / "second"):
        state = open_export(output).state("selected")
        cell = cast(
            dict[str, Any],
            decode_json_object(state.output("cell").asset_bytes(), "cell snapshot"),
        )
        assert state.output("value").json() == "scale=3"
        assert cell["console"] == [
            {
                "channel": "stdout",
                "data": "scale=3\n",
                "mimetype": "text/plain",
            }
        ]


def test_ui_callback_state_updates_execute_dynamically_stale_projection(
    tmp_path: Path,
) -> None:
    counter = tmp_path / "runs.txt"
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        f"""
import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as _mo
    get_selected, set_selected = _mo.state(0)
    return get_selected, set_selected


@app.cell
def _(set_selected):
    import marimo as _mo
    slider = _mo.ui.slider(0, 5, value=1, on_change=set_selected)
    return (slider,)


@app.cell
def _(get_selected):
    from pathlib import Path
    metric = get_selected()
    path = Path({str(counter)!r})
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"{{metric}}\\n")
    print(f"metric={{metric}}")
    return (metric,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    spec = ExportSpec(
        default_state="selected",
        states={"selected": {"slider": 3}},
        outputs={"metric": OutputSpec.json("metric")},
    )

    with open_notebook(notebook, timeout=30) as producer:
        counter.write_text("", encoding="utf-8")
        _capture_session(producer._require_open(), spec, tmp_path / "export")

    state = open_export(tmp_path / "export").state("selected")
    assert counter.read_text(encoding="utf-8") == "3\n"
    assert state.output("metric").json() == 3


def test_composed_control_mapping_matches_snapshot_resources_across_states(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def control_cell():
    import marimo as mo
    lower = mo.ui.slider(0, 5, value=1)
    upper = mo.ui.slider(10, 15, value=11)
    controls = mo.ui.array([lower, upper])
    controls
    return controls, lower, upper


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    result = build(
        notebook,
        spec=ExportSpec(
            default_state="first",
            states={
                "first": {"controls": {"0": 2, "1": 12}},
                "second": {"controls": {"0": 3, "1": 13}},
            },
            outputs={
                "controls": OutputSpec.cell("control_cell"),
            },
        ),
        output=tmp_path / "export",
        timeout=30,
    )
    notebook_export = open_export(result.path)

    assert len(notebook_export.control_bindings) == 3
    assert {binding.input for binding in notebook_export.control_bindings.values()} == {"controls"}
    assert {binding.path for binding in notebook_export.control_bindings.values()} == {
        (),
        (ControlIndexStep(value=0),),
        (ControlIndexStep(value=1),),
    }
    for alias, selected in (
        ("first", {"0": 2, "1": 12}),
        ("second", {"0": 3, "1": 13}),
    ):
        state = notebook_export.state(alias)
        snapshot = cast(
            dict[str, Any],
            decode_json_object(state.output("controls").asset_bytes(), "cell snapshot"),
        )
        control_ids = set(notebook_export.control_bindings)
        assert state.inputs["controls"] == selected
        assert set(snapshot["resources"]["functions"]) == control_ids
        assert set(snapshot["resources"]["uiValues"]) == control_ids
