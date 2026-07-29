from __future__ import annotations

from pathlib import Path

import pytest
from marimo_export import ExportSpec, OutputSpec, capture, open_publication
from marimo_export._remote.managed import ManagedServer
from marimo_export.errors import ExecutionError


def _capture(notebook: Path, spec: ExportSpec, output: Path) -> None:
    server = ManagedServer(notebook, timeout=30)
    try:
        server.activate()
        capture(
            server.base_url,
            session=server.session_id,
            access_token=server.access_token,
            spec=spec,
            output=output,
            timeout=30,
        )
    finally:
        server.stop()


def test_ordinary_input_siblings_are_isolated_from_every_state_and_parent(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    shared = []
    x = 0
    return shared, x


@app.cell
def _(shared, x):
    shared.append(x)
    snapshot = ",".join(str(value) for value in shared)
    return (snapshot,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    source = notebook.read_bytes()
    spec = ExportSpec(
        inputs=("x",),
        states={"one": {"x": 1}, "two": {"x": 2}},
        outputs={"snapshot": OutputSpec(source="snapshot")},
    )
    server = ManagedServer(notebook, timeout=30)
    try:
        server.activate()
        first = capture(
            server.base_url,
            session=server.session_id,
            access_token=server.access_token,
            spec=spec,
            output=tmp_path / "first",
            timeout=30,
        )
        second = capture(
            server.base_url,
            session=server.session_id,
            access_token=server.access_token,
            spec=spec,
            output=tmp_path / "second",
            timeout=30,
        )
    finally:
        server.stop()

    for result in (first, second):
        publication = open_publication(result.path)
        assert publication.state("one").output("snapshot").scalar() == "0,1"
        assert publication.state("two").output("snapshot").scalar() == "0,2"
    assert notebook.read_bytes() == source


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
        inputs=("selector",),
        states={"failure": {"selector": ["fail"]}},
        outputs={"stable": OutputSpec(source="stable")},
    )
    output = tmp_path / "publication"

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
        inputs=("selector",),
        states={"failure": {"selector": 2}},
        outputs={"stable": OutputSpec(source="stable")},
    )
    output = tmp_path / "publication"

    with pytest.raises(ExecutionError) as raised:
        _capture(notebook, spec, output)

    assert raised.value.code == "input_value_invalid"
    assert raised.value.details["input"] == "selector"
    assert not output.exists()
