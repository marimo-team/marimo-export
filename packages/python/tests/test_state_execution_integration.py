from __future__ import annotations

from pathlib import Path

import pytest
from marimo_export import ExportSpec, OutputSpec, build, capture, open_export
from marimo_export._build import inspect_notebook
from marimo_export._remote.managed import ManagedServer
from marimo_export.errors import ExecutionError
from marimo_export.exporters import anywidget as anywidget_exporter
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


def test_ordinary_input_state_is_isolated_across_states_and_captures(
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

    def record(value):
        shared.append(value)
        record.calls.append(value)

    record.calls = []

    class Bucket:
        values = []

    x = 0
    return Bucket, record, shared, x


@app.cell
def _(Bucket, record, shared, x):
    record(x)
    Bucket.values.append(x)
    snapshot = f"{shared}:{record.calls}:{Bucket.values}"
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
        export = open_export(result.path)
        assert export.state("one").output("snapshot").scalar() == "[1]:[1]:[1]"
        assert export.state("two").output("snapshot").scalar() == "[2]:[2]:[2]"
    assert notebook.read_bytes() == source


def test_capture_preserves_the_parent_lazy_cache_store(
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
    value = 1
    return (value,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    (tmp_path / "cache_probe.py").write_text(
        """
def describe(value):
    del value
    from marimo._runtime.context import get_context

    context = get_context()
    while context.parent is not None:
        context = context.parent
    loader = context.cache.active_lazy_loaders["cell_cache"]
    store = loader.store
    inner = type(getattr(store, "_inner", None)).__name__
    return f"{type(store).__name__}:{inner}:{len(store.export_keys())}"
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    spec = ExportSpec(
        inputs=(),
        states={"baseline": {}},
        outputs={
            "cache": OutputSpec(
                source="value",
                exporter=importable("cache_probe:describe"),
            )
        },
    )
    server = ManagedServer(notebook, timeout=30)
    try:
        server.activate()
        results = [
            capture(
                server.base_url,
                session=server.session_id,
                access_token=server.access_token,
                spec=spec,
                output=tmp_path / f"export-{position}",
                timeout=30,
            )
            for position in range(2)
        ]
    finally:
        server.stop()

    for result in results:
        value = open_export(result.path).state("baseline").output("cache").scalar()
        assert isinstance(value, str)
        store, inner, touched = value.split(":")
        assert (store, inner) == ("LazyStore", "FileStore")
        assert int(touched) > 0


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
            inputs=("counter",),
            states={"baseline": {}, "raised": {"counter": {"count": 7}}},
            outputs={"summary": OutputSpec(source="summary")},
        ),
        output=tmp_path / "export",
        timeout=30,
    )
    export = open_export(result.path)

    assert export.state("baseline").inputs == {"counter": {"count": 2, "label": "ready"}}
    assert export.state("raised").inputs == {"counter": {"count": 7, "label": "ready"}}
    assert export.state("baseline").output("summary").scalar() == "2:ready"
    assert export.state("raised").output("summary").scalar() == "7:ready"
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
                inputs=("toggle",),
                states={"enabled": {"toggle": {"flag": 1}}},
                outputs={"flag": OutputSpec(source="flag")},
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
    assert widget.input_mode == "patch"
    assert not widget.portable_input
    result = build(
        notebook,
        spec=ExportSpec(
            inputs=(),
            states={"baseline": {}},
            outputs={
                "widget": OutputSpec(
                    source="widget",
                    exporter=anywidget_exporter.bundle(),
                )
            },
        ),
        output=tmp_path / "export",
        timeout=30,
    )
    payload = open_export(result.path).state("baseline").output("widget").blob_asset().data

    assert b'"buffer_paths":[["binary"]]' in payload
    assert b'"buffers":["AQID"]' in payload


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
                exporter=importable("export_exports:count_children"),
            )
        },
    )
    failure_spec = ExportSpec(
        inputs=("x",),
        states={"one": {"x": 1}, "two": {"x": 2}},
        outputs={
            "value": OutputSpec(
                source="x",
                exporter=importable("export_exports:fail_on_two"),
            )
        },
    )
    after_failure_spec = ExportSpec(
        inputs=("x",),
        states={"four": {"x": 4}, "five": {"x": 5}, "six": {"x": 6}},
        outputs=count_spec.outputs,
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
            spec=after_failure_spec,
            output=tmp_path / "after-failure",
            timeout=30,
        )
    finally:
        server.stop()

    for result, names in (
        (first, ("one", "two", "three")),
        (after_failure, ("four", "five", "six")),
    ):
        export = open_export(result.path)
        assert [export.state(name).output("children").scalar() for name in names] == [1, 1, 1]
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
        inputs=("x",),
        states={"one": {"x": 1}, "two": {"x": 2}},
        outputs={"result": OutputSpec(source="result")},
    )

    _capture(notebook, spec, tmp_path / "export")
    export = open_export(tmp_path / "export")

    assert export.state("one").output("result").scalar() == "1:1"
    assert export.state("two").output("result").scalar() == "2:1"


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
        inputs=("selector",),
        states={"failure": {"selector": 2}},
        outputs={"stable": OutputSpec(source="stable")},
    )
    output = tmp_path / "export"

    with pytest.raises(ExecutionError) as raised:
        _capture(notebook, spec, output)

    assert raised.value.code == "input_value_invalid"
    assert raised.value.details["input"] == "selector"
    assert not output.exists()
