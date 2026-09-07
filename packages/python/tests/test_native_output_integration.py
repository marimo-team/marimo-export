from __future__ import annotations

from collections import Counter
from io import BytesIO
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest
from export_integration_support import build
from marimo_export import ExportSpec, OutputSpec, open_export
from marimo_export.descriptors import ArrowDescriptor, NumpyDescriptor
from marimo_export.errors import OutputError


def _write_notebook(notebook: Path, counter: Path) -> None:
    notebook.write_text(
        f"""
import marimo

app = marimo.App()


@app.cell
def _():
    import numpy as np
    import polars as pl
    from pathlib import Path

    counter = Path({str(counter)!r})
    counter.write_text(counter.read_text() + 'x' if counter.exists() else 'x')
    array = np.array([[1, 2], [3, 4]], dtype=np.int64)
    record = {{"rows": [{{"name": "alpha", "value": 1}}]}}
    scalar = 42
    table = pl.DataFrame({{"name": ["alpha", "beta"], "value": [1, 2]}})
    return array, record, scalar, table


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )


def test_native_values_survive_warm_builds_and_changed_output_plans(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    counter = tmp_path / "runs.txt"
    _write_notebook(notebook, counter)
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={
            "array": OutputSpec.native("array"),
            "record": OutputSpec.native("record"),
            "scalar": OutputSpec.native("scalar"),
            "table": OutputSpec.native("table"),
        },
    )

    first = build(notebook, spec=spec, output=tmp_path / "first", timeout=30)
    second = build(notebook, spec=spec, output=tmp_path / "second", timeout=30)
    assert counter.read_text() == "x"

    for result in (first, second):
        state = open_export(result.path).state("baseline")
        assert isinstance(state.output("array").descriptor, NumpyDescriptor)
        assert np.load(BytesIO(state.output("array").asset_bytes())).tolist() == [[1, 2], [3, 4]]
        assert state.output("record").json() == {"rows": ({"name": "alpha", "value": 1},)}
        assert state.output("scalar").scalar() == 42
        assert isinstance(state.output("table").descriptor, ArrowDescriptor)
        assert pa.ipc.open_stream(state.output("table").asset_bytes()).read_all().to_pylist() == [
            {"name": "alpha", "value": 1},
            {"name": "beta", "value": 2},
        ]
        assert state.output("table").descriptor.provenance.python_type == (
            "polars.dataframe.frame.DataFrame"
        )
    assert first.cache_activity.projection_misses == 4
    assert second.cache_activity.projection_hits == 4
    changed_spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={"answer": OutputSpec.native("scalar")},
    )
    changed = build(notebook, spec=changed_spec, output=tmp_path / "changed", timeout=30)
    assert open_export(changed.path).state("baseline").output("answer").scalar() == 42
    assert counter.read_text() == "x"


def test_different_output_plans_reuse_every_prepared_ui_state(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    counter = tmp_path / "runs.txt"
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
    counter = Path({str(counter)!r})
    runs = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(runs))
    value = scale.value * 10
    return (value,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )

    def spec(output: str) -> ExportSpec:
        return ExportSpec(
            default_state="one",
            states={"one": {"scale": 1}, "three": {"scale": 3}},
            outputs={output: OutputSpec.native("value")},
        )

    build(notebook, spec=spec("first"), output=tmp_path / "first", timeout=30)
    runs_after_first_plan = counter.read_text(encoding="utf-8")
    second = build(
        notebook,
        spec=spec("second"),
        output=tmp_path / "second",
        timeout=30,
    )

    assert runs_after_first_plan == "2"
    assert counter.read_text(encoding="utf-8") == runs_after_first_plan
    assert second.cache_activity.authored_hits >= 2
    opened = open_export(second.path)
    assert opened.state("one").output("second").scalar() == 10
    assert opened.state("three").output("second").scalar() == 30


@pytest.mark.application
def test_dependency_edits_refresh_native_outputs_and_preserve_state_combination_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = tmp_path / "notebook.py"
    helper = tmp_path / "calculation.py"
    counter = tmp_path / "calls.txt"
    monkeypatch.syspath_prepend(str(tmp_path))

    def write_helper(factor: int) -> None:
        helper.write_text(
            f"""
from pathlib import Path

class Result:
    def __init__(self, value):
        self.value = value

def calculate(value, source):
    counter = Path({str(counter)!r})
    with counter.open('a') as stream:
        stream.write(source + '\\n')
    return Result(value * {factor})
""".lstrip(),
            encoding="utf-8",
        )

    notebook.write_text(
        """
import marimo
app = marimo.App()

@app.cell
def inputs():
    import calculation
    x = 1
    y = 1
    return calculation, x, y

@app.cell
def calculate(calculation, x):
    result = calculation.calculate(x, 'upstream')
    return (result,)

@app.cell
def calculate_locally(x):
    import calculation as _calculation
    local_result = _calculation.calculate(x, 'local')
    return (local_result,)

@app.cell
def output(result, local_result, y):
    value = result.value + y
    local_value = local_result.value + y
    return value, local_value

if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    original = notebook.read_bytes()

    def spec(output: str, *, expanded: bool = False) -> ExportSpec:
        states = {"one": {"x": 1, "y": 1}, "two": {"x": 2, "y": 1}}
        if expanded:
            states["combined"] = {"x": 1, "y": 2}
        return ExportSpec(
            default_state="one",
            states=states,
            outputs={
                f"{output}_upstream": OutputSpec.native("value"),
                f"{output}_local": OutputSpec.native("local_value"),
            },
        )

    write_helper(10)
    first = build(notebook, spec=spec("first"), output=tmp_path / "first", timeout=30)
    first_state = open_export(first.path).state("two")
    assert first_state.output("first_upstream").scalar() == 21
    assert first_state.output("first_local").scalar() == 21
    assert Counter(counter.read_text().splitlines()) == {"upstream": 2, "local": 2}

    second = build(
        notebook,
        spec=spec("second", expanded=True),
        output=tmp_path / "second",
        timeout=30,
    )
    combined = open_export(second.path).state("combined")
    assert combined.output("second_upstream").scalar() == 12
    assert combined.output("second_local").scalar() == 12
    assert Counter(counter.read_text().splitlines()) == {"upstream": 2, "local": 2}

    edited = original.replace(b"    y = 1", b"    y = 2")
    notebook.write_bytes(edited)
    notebook_edit = build(
        notebook,
        spec=spec("second", expanded=True),
        output=tmp_path / "notebook-edit",
        timeout=30,
    )
    edited_state = open_export(notebook_edit.path).state("combined")
    assert edited_state.output("second_upstream").scalar() == 12
    assert edited_state.output("second_local").scalar() == 12
    assert Counter(counter.read_text().splitlines()) == {"upstream": 2, "local": 2}

    write_helper(100)
    changed = build(
        notebook,
        spec=spec("second", expanded=True),
        output=tmp_path / "changed",
        timeout=30,
    )
    changed_export = open_export(changed.path)
    for output in ("second_upstream", "second_local"):
        assert changed_export.state("one").output(output).scalar() == 101
        assert changed_export.state("two").output(output).scalar() == 201
        assert changed_export.state("combined").output(output).scalar() == 102
    assert Counter(counter.read_text().splitlines()) == {"upstream": 4, "local": 4}
    assert notebook.read_bytes() == edited


def test_native_output_rejects_a_pickle_representation(tmp_path: Path) -> None:
    notebook = tmp_path / "unsupported.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    unsupported = 1 + 2j
    return (unsupported,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={"unsupported": OutputSpec.native("unsupported")},
    )

    with pytest.raises(OutputError) as raised:
        build(notebook, spec=spec, output=tmp_path / "export", timeout=30)

    assert raised.value.code == "output_execution_failed"
    assert not (tmp_path / "export").exists()
