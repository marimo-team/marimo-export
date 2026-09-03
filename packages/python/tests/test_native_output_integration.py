from __future__ import annotations

from pathlib import Path

import pytest
from export_integration_support import build
from marimo_export import ExportSpec, OutputSpec, open_export
from marimo_export.descriptors import ArrowDescriptor, NumpyDescriptor
from marimo_export.errors import OutputError


def _write_notebook(notebook: Path) -> None:
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import numpy as np
    import polars as pl

    array = np.array([[1, 2], [3, 4]], dtype=np.int64)
    record = {"rows": [{"name": "alpha", "value": 1}]}
    scalar = 42
    table = pl.DataFrame({"name": ["alpha", "beta"], "value": [1, 2]})
    return array, record, scalar, table


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )


def test_native_outputs_preserve_json_and_cache_native_values_across_warm_builds(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    _write_notebook(notebook)
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

    for result in (first, second):
        state = open_export(result.path).state("baseline")
        assert isinstance(state.output("array").descriptor, NumpyDescriptor)
        assert state.output("record").json() == {"rows": ({"name": "alpha", "value": 1},)}
        assert state.output("scalar").scalar() == 42
        assert isinstance(state.output("table").descriptor, ArrowDescriptor)
        assert state.output("table").descriptor.provenance.python_type == (
            "polars.dataframe.frame.DataFrame"
        )
    assert first.cache_activity.projection_misses == 4
    assert second.cache_activity.projection_hits == 4


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
