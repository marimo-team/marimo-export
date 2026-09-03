from __future__ import annotations

import json
from pathlib import Path

import pytest
from export_integration_support import (
    build,
)
from export_integration_support import (
    capture_session as _capture_session,
)
from marimo_export import (
    ExportSpec,
    OutputSpec,
    open_export,
)
from marimo_export._identity import implementation_identity
from marimo_export.errors import ExecutionError
from marimo_export.exporters import anywidget as anywidget_exporter
from marimo_export.inspection import inspect_notebook
from marimo_export.producer import open_notebook


def test_managed_sparse_anywidget_state_records_complete_widget_traits(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import anywidget
    import marimo as mo
    import traitlets

    class Counter(anywidget.AnyWidget):
        _esm = "export default { render() {} }"
        count = traitlets.Int(2).tag(sync=True)
        label = traitlets.Unicode("ready").tag(sync=True)

    counter = mo.ui.anywidget(Counter())
    return (counter,)


@app.cell
def _(counter):
    summary = f'{counter.value["count"]}:{counter.value["label"]}'
    return (summary,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    source = notebook.read_bytes()
    result = build(
        notebook,
        spec=ExportSpec(
            default_state="baseline",
            states={"baseline": {}, "raised": {"counter": {"count": 7}}},
            outputs={"summary": OutputSpec.json("summary")},
        ),
        output=tmp_path / "export",
        timeout=30,
    )
    export = open_export(result.path)

    assert export.state("baseline").inputs == {"counter": {"count": 2, "label": "ready"}}
    assert export.state("raised").inputs == {"counter": {"count": 7, "label": "ready"}}
    assert export.state("baseline").output("summary").json() == "2:ready"
    assert export.state("raised").output("summary").json() == "7:ready"
    assert notebook.read_bytes() == source


def test_managed_anywidget_state_rejects_json_type_coercion(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import anywidget
    import marimo as mo
    import traitlets

    class Toggle(anywidget.AnyWidget):
        _esm = "export default { render() {} }"
        flag = traitlets.Bool(False).tag(sync=True)

    toggle = mo.ui.anywidget(Toggle())
    return (toggle,)


@app.cell
def _(toggle):
    flag = toggle.value["flag"]
    return (flag,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    output = tmp_path / "export"

    with pytest.raises(ExecutionError) as raised:
        build(
            notebook,
            spec=ExportSpec(
                default_state="enabled",
                states={"enabled": {"toggle": {"flag": 1}}},
                outputs={"flag": OutputSpec.json("flag")},
            ),
            output=output,
            timeout=30,
        )

    assert raised.value.code == "input_value_invalid"
    assert raised.value.details == {"state": "enabled", "input": "toggle"}
    assert not output.exists()


def test_binary_anywidget_is_output_capable_and_not_a_portable_input(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import anywidget
    import marimo as mo
    import traitlets

    class BinaryWidget(anywidget.AnyWidget):
        _esm = "export default { render() {} }"
        binary = traitlets.Bytes(b"\\x01\\x02\\x03").tag(sync=True)

    widget = mo.ui.anywidget(BinaryWidget())
    return (widget,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )

    description = inspect_notebook(notebook, timeout=30)
    widget = next(item for item in description.definitions if item.name == "widget")
    assert description.implementation_sha256 == implementation_identity()
    assert widget.input_mode == "patch"
    assert not widget.portable_input
    assert len(description.cells) == 1
    assert description.cells[0].id == widget.cell_id
    assert description.cells[0].name is None
    assert len(description.cells[0].code_sha256) == 64
    result = build(
        notebook,
        spec=ExportSpec(
            default_state="baseline",
            states={"baseline": {}},
            outputs={
                "widget": OutputSpec.export(
                    "widget",
                    anywidget_exporter.bundle(),
                )
            },
        ),
        output=tmp_path / "export",
        timeout=30,
    )
    payload = open_export(result.path).state("baseline").output("widget").blob_asset().data

    assert b'"buffer_paths":[["binary"]]' in payload
    assert b'"buffers":["AQID"]' in payload


def test_anywidget_bundle_refreshes_live_model_state_across_owned_captures(
    tmp_path: Path,
) -> None:
    state_file = tmp_path / "value.txt"
    state_file.write_text("1", encoding="utf-8")
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        f"""
import marimo

app = marimo.App()


@app.cell
def _():
    from pathlib import Path
    import anywidget
    import marimo as mo
    import traitlets

    class Counter(anywidget.AnyWidget):
        _esm = "export default {{ render() {{}} }}"
        value = traitlets.Int().tag(sync=True)
        marker = traitlets.Bytes(b"\\x00").tag(sync=True)

    widget = mo.ui.anywidget(Counter(value=int(Path({str(state_file)!r}).read_text())))
    return (widget,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={
            "widget": OutputSpec.export(
                "widget",
                anywidget_exporter.bundle(),
            )
        },
    )

    with open_notebook(notebook, timeout=30) as producer:
        session = producer._require_open()
        first_activity = _capture_session(session, spec, tmp_path / "first")
        state_file.write_text("2", encoding="utf-8")
        second_activity = _capture_session(session, spec, tmp_path / "second")

    def model_values(path: Path) -> list[object]:
        payload = json.loads(open_export(path).state("baseline").output("widget").blob_asset().data)
        return [
            notification["message"]["state"].get("value")
            for notification in payload["modelNotifications"]
            if notification["message"]["method"] == "open"
        ]

    assert (first_activity.projection_hits, first_activity.projection_misses) == (0, 1)
    assert (second_activity.projection_hits, second_activity.projection_misses) == (0, 1)
    assert open_export(tmp_path / "first").state("baseline").inputs == {}
    assert open_export(tmp_path / "second").state("baseline").inputs == {}
    assert 1 in model_values(tmp_path / "first")
    assert 2 in model_values(tmp_path / "second")
