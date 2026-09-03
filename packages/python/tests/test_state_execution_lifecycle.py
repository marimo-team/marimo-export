from __future__ import annotations

from pathlib import Path

import pytest
from export_integration_support import (
    capture_export as _capture,
)
from export_integration_support import (
    capture_live as _capture_live,
)
from marimo_export import (
    ExportSpec,
    OutputSpec,
    open_export,
)
from marimo_export._remote.managed import ManagedServer
from marimo_export.errors import ExecutionError
from marimo_export.exporters import importable


def test_state_children_leave_the_parent_after_success_and_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    x = 0
    return (x,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    (tmp_path / "export_exports.py").write_text(
        """
from marimo._runtime.context import get_context
from marimo_export.outputs import BlobAsset


def count_children(value):
    del value
    context = get_context()
    return BlobAsset(
        data=str(len(context.parent.children)).encode(),
        media_type="text/plain",
    )


def fail_on_two(value):
    if value == 2:
        raise RuntimeError("expected failure")
    return BlobAsset(data=str(value).encode(), media_type="text/plain")
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    count_spec = ExportSpec(
        default_state="one",
        states={"one": {"x": 1}, "two": {"x": 2}, "three": {"x": 3}},
        outputs={
            "children": OutputSpec.export(
                "x",
                importable("export_exports:count_children"),
            )
        },
    )
    failure_spec = ExportSpec(
        default_state="one",
        states={"one": {"x": 1}, "two": {"x": 2}},
        outputs={
            "value": OutputSpec.export(
                "x",
                importable("export_exports:fail_on_two"),
            )
        },
    )
    after_failure_spec = ExportSpec(
        default_state="four",
        states={"four": {"x": 4}, "five": {"x": 5}, "six": {"x": 6}},
        outputs=count_spec.outputs,
    )
    server = ManagedServer(notebook, timeout=30)
    try:
        server.activate()
        _capture_live(server, count_spec, tmp_path / "first")
        with pytest.raises(ExecutionError):
            _capture_live(server, failure_spec, tmp_path / "failure")
        _capture_live(server, after_failure_spec, tmp_path / "after-failure")
    finally:
        server.stop()

    for output, names in (
        (tmp_path / "first", ("one", "two", "three")),
        (tmp_path / "after-failure", ("four", "five", "six")),
    ):
        export = open_export(output)
        assert [int(export.state(name).output("children").blob_asset().data) for name in names] == [
            1,
            1,
            1,
        ]
    assert not (tmp_path / "failure").exists()


def test_ordinary_input_can_share_its_authored_cell_with_a_ui_element(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as mo

    selector = mo.ui.slider(0, 2, value=1)
    x = 0
    return selector, x


@app.cell
def _(selector, x):
    result = f"{x}:{selector.value}"
    return (result,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    spec = ExportSpec(
        default_state="one",
        states={"one": {"x": 1}, "two": {"x": 2}},
        outputs={"result": OutputSpec.json("result")},
    )

    _capture(notebook, spec, tmp_path / "export")
    export = open_export(tmp_path / "export")

    assert export.state("one").output("result").json() == "1:1"
    assert export.state("two").output("result").json() == "2:1"


def test_ui_dependent_cell_failure_rejects_the_state(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as mo
    selector = mo.ui.dropdown(["ready", "fail"], value="ready")
    stable = 42
    return selector, stable


@app.cell
def _(selector):
    if selector.value == "fail":
        raise RuntimeError("dependent cell failed")
    dependent = selector.value
    return (dependent,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    spec = ExportSpec(
        default_state="failure",
        states={"failure": {"selector": ["fail"]}},
        outputs={"stable": OutputSpec.json("stable")},
    )
    output = tmp_path / "export"

    with pytest.raises(ExecutionError) as raised:
        _capture(notebook, spec, output)

    assert raised.value.code == "state_execution_failed"
    assert not output.exists()


def test_ui_callback_failure_rejects_the_state(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as mo

    def reject(value):
        if value == 2:
            raise RuntimeError("callback rejected value")

    selector = mo.ui.slider(1, 2, value=1, on_change=reject)
    stable = 42
    return selector, stable


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    spec = ExportSpec(
        default_state="failure",
        states={"failure": {"selector": 2}},
        outputs={"stable": OutputSpec.json("stable")},
    )
    output = tmp_path / "export"

    with pytest.raises(ExecutionError) as raised:
        _capture(notebook, spec, output)

    assert raised.value.code == "input_value_invalid"
    assert raised.value.details["input"] == "selector"
    assert not output.exists()
