from __future__ import annotations

from pathlib import Path

import pytest
from marimo_export import ExportSpec, OutputSpec, capture, open_publication
from marimo_export._remote.managed import ManagedServer
from marimo_export.errors import ExecutionError
from marimo_export.exporters import importable


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


def test_ordinary_input_cell_executes_fresh_in_every_state(
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
        assert publication.state("one").output("snapshot").scalar() == "1"
        assert publication.state("two").output("snapshot").scalar() == "2"
    assert notebook.read_bytes() == source


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
    (tmp_path / "publication_exports.py").write_text(
        """
from marimo._runtime.context import get_context


def count_children(value):
    del value
    context = get_context()
    return len(context.parent.children)


def fail_on_two(value):
    if value == 2:
        raise RuntimeError("expected failure")
    return value
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    count_spec = ExportSpec(
        inputs=("x",),
        states={"one": {"x": 1}, "two": {"x": 2}, "three": {"x": 3}},
        outputs={
            "children": OutputSpec(
                source="x",
                exporter=importable("publication_exports:count_children"),
            )
        },
    )
    failure_spec = ExportSpec(
        inputs=("x",),
        states={"one": {"x": 1}, "two": {"x": 2}},
        outputs={
            "value": OutputSpec(
                source="x",
                exporter=importable("publication_exports:fail_on_two"),
            )
        },
    )
    server = ManagedServer(notebook, timeout=30)
    try:
        server.activate()
        first = capture(
            server.base_url,
            session=server.session_id,
            access_token=server.access_token,
            spec=count_spec,
            output=tmp_path / "first",
            timeout=30,
        )
        with pytest.raises(ExecutionError):
            capture(
                server.base_url,
                session=server.session_id,
                access_token=server.access_token,
                spec=failure_spec,
                output=tmp_path / "failure",
                timeout=30,
            )
        after_failure = capture(
            server.base_url,
            session=server.session_id,
            access_token=server.access_token,
            spec=count_spec,
            output=tmp_path / "after-failure",
            timeout=30,
        )
    finally:
        server.stop()

    for result in (first, after_failure):
        publication = open_publication(result.path)
        assert [
            publication.state(name).output("children").scalar() for name in ("one", "two", "three")
        ] == [1, 1, 1]
    assert not (tmp_path / "failure").exists()


def test_callable_and_class_siblings_are_owned_by_each_state_child(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    values = []

    def record(value):
        values.append(value)
        record.calls.append(value)

    record.calls = []

    class Bucket:
        values = []

    x = 0
    return Bucket, record, values, x


@app.cell
def _(Bucket, record, values, x):
    record(x)
    Bucket.values.append(x)
    snapshot = f"{values}:{record.calls}:{Bucket.values}"
    return (snapshot,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    spec = ExportSpec(
        inputs=("x",),
        states={"one": {"x": 1}, "two": {"x": 2}},
        outputs={"snapshot": OutputSpec(source="snapshot")},
    )

    _capture(notebook, spec, tmp_path / "publication")
    publication = open_publication(tmp_path / "publication")

    assert publication.state("one").output("snapshot").scalar() == "[1]:[1]:[1]"
    assert publication.state("two").output("snapshot").scalar() == "[2]:[2]:[2]"


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
        inputs=("x",),
        states={"one": {"x": 1}, "two": {"x": 2}},
        outputs={"result": OutputSpec(source="result")},
    )

    _capture(notebook, spec, tmp_path / "publication")
    publication = open_publication(tmp_path / "publication")

    assert publication.state("one").output("result").scalar() == "1:1"
    assert publication.state("two").output("result").scalar() == "2:1"


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
