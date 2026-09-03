from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from export_integration_support import build
from marimo_export import ExportSpec, OutputSpec, open_export
from marimo_export._json import decode_json_object


def _write_external_dependency_notebook(
    notebook: Path,
    source: Path,
    upstream_counter: Path,
    downstream_counter: Path,
) -> None:
    notebook.write_text(
        f"""
import marimo

app = marimo.App()


@app.cell
def source_cell():
    from pathlib import Path as SourcePath
    source_counter = SourcePath({str(upstream_counter)!r})
    source_runs = int(source_counter.read_text()) + 1 if source_counter.exists() else 1
    source_counter.write_text(str(source_runs))
    source_value = SourcePath({str(source)!r}).read_text()
    return (source_value,)


@app.cell
def report(source_value):
    import marimo as mo
    from pathlib import Path as ReportPath
    report_counter = ReportPath({str(downstream_counter)!r})
    report_runs = int(report_counter.read_text()) + 1 if report_counter.exists() else 1
    report_counter.write_text(str(report_runs))
    summary = f"summary={{source_value}}"
    print(summary)
    view = mo.md(summary)
    view
    return summary, view


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )


def test_complete_cell_records_native_output_and_console_across_warm_captures(
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
    from marimo._runtime.context import get_context
    literal_cell_id = str(get_context().cell_id)
    value = 42
    return literal_cell_id, mo, value


@app.cell
def report(literal_cell_id, mo, value):
    print(f"value={value}")
    print(f"cell={literal_cell_id}")
    mo.md(f"# Value {value}")
    return


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={
            "literal": OutputSpec.json("literal_cell_id"),
            "report": OutputSpec.cell("report"),
        },
    )

    first = build(notebook, spec=spec, output=tmp_path / "first", timeout=30)
    second = build(notebook, spec=spec, output=tmp_path / "second", timeout=30)

    for result in (first, second):
        snapshot = cast(
            dict[str, Any],
            decode_json_object(
                open_export(result.path).state("baseline").output("report").asset_bytes(),
                "cell snapshot",
            ),
        )
        literal = open_export(result.path).state("baseline").output("literal").json()
        assert snapshot["outcome"] == "completed"
        assert snapshot["output"] == {
            "channel": "output",
            "mimetype": "text/markdown",
            "data": (
                '<span class="markdown prose dark:prose-invert contents">'
                '<h1 id="value-42">Value 42</h1></span>'
            ),
        }
        assert snapshot["console"] == [
            {
                "channel": "stdout",
                "mimetype": "text/plain",
                "data": f"value=42\ncell={literal}\n",
            }
        ]
    assert (
        second.cache_activity.authored_hits,
        second.cache_activity.authored_misses,
    ) == (0, 2)


def test_complete_cell_force_uses_the_final_requested_state_cache_disposition(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    counter = tmp_path / "runs.txt"

    def write_notebook(default: int) -> None:
        notebook.write_text(
            f"""
import marimo

app = marimo.App()


@app.cell
def controls():
    import marimo as mo
    slider = mo.ui.slider(0, 5, value={default})
    return mo, slider


@app.cell
def report(mo, slider):
    from pathlib import Path
    counter = Path({str(counter)!r})
    runs = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(runs))
    print(f"value={{slider.value}}")
    mo.md(f"value={{slider.value}}")
    return


if __name__ == "__main__":
    app.run()
""".lstrip(),
            encoding="utf-8",
        )

    def spec(value: int) -> ExportSpec:
        return ExportSpec(
            default_state="selected",
            states={"selected": {"slider": value}},
            outputs={"report": OutputSpec.cell("report")},
        )

    def console_value(path: Path) -> str:
        snapshot = cast(
            dict[str, Any],
            decode_json_object(
                open_export(path).state("selected").output("report").asset_bytes(),
                "cell snapshot",
            ),
        )
        return snapshot["console"][0]["data"]

    write_notebook(default=1)
    build(notebook, spec=spec(2), output=tmp_path / "warm", timeout=30)

    counter.write_text("0", encoding="utf-8")
    final_miss = build(notebook, spec=spec(3), output=tmp_path / "final-miss", timeout=30)
    assert counter.read_text(encoding="utf-8") == "1"
    assert console_value(tmp_path / "final-miss") == "value=3\n"
    assert (
        final_miss.cache_activity.authored_hits,
        final_miss.cache_activity.authored_misses,
    ) == (0, 2)

    write_notebook(default=4)
    counter.write_text("0", encoding="utf-8")
    final_hit = build(notebook, spec=spec(3), output=tmp_path / "final-hit", timeout=30)
    assert counter.read_text(encoding="utf-8") == "2"
    assert console_value(tmp_path / "final-hit") == "value=3\n"
    assert (
        final_hit.cache_activity.authored_hits,
        final_hit.cache_activity.authored_misses,
    ) == (0, 2)


def test_upstream_code_change_invalidates_value_output_and_cell_projections(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"

    def write_notebook(seed: int) -> None:
        notebook.write_text(
            f"""
import marimo

app = marimo.App()


@app.cell
def _():
    seed = {seed}
    return (seed,)


@app.cell
def report(seed):
    import marimo as mo
    value = f"value={{seed}}"
    print(value)
    view = mo.md(value)
    view
    return value, view


if __name__ == "__main__":
    app.run()
""".lstrip(),
            encoding="utf-8",
        )

    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={
            "value": OutputSpec.json("value"),
            "output": OutputSpec.output("view"),
            "cell": OutputSpec.cell("report"),
        },
    )
    write_notebook(1)
    first = build(notebook, spec=spec, output=tmp_path / "first", timeout=30)
    write_notebook(2)
    second = build(notebook, spec=spec, output=tmp_path / "second", timeout=30)

    for result, expected in ((first, "value=1"), (second, "value=2")):
        state = open_export(result.path).state("baseline")
        output = cast(
            dict[str, Any],
            decode_json_object(state.output("output").asset_bytes(), "output snapshot"),
        )
        cell = cast(
            dict[str, Any],
            decode_json_object(state.output("cell").asset_bytes(), "cell snapshot"),
        )
        assert state.output("value").json() == expected
        assert expected in output["output"]["data"]
        assert expected in cell["output"]["data"]
        assert cell["console"][0]["data"] == f"{expected}\n"
    first_cell = open_export(first.path).state("baseline").output("cell").asset_bytes()
    second_cell = open_export(second.path).state("baseline").output("cell").asset_bytes()
    assert first_cell != second_cell


def test_same_document_external_change_invalidates_all_projection_receipts(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    source = tmp_path / "value.txt"
    notebook.write_text(
        f"""
import marimo

app = marimo.App()


@app.cell
def report():
    import marimo as mo
    from pathlib import Path
    value = Path({str(source)!r}).read_text()
    print(value)
    view = mo.md(value)
    view
    return value, view


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={
            "value": OutputSpec.json("value"),
            "output": OutputSpec.output("view"),
            "cell": OutputSpec.cell("report"),
        },
    )
    source.write_text("first", encoding="utf-8")
    first = build(notebook, spec=spec, output=tmp_path / "first", timeout=30)
    source.write_text("second", encoding="utf-8")
    second = build(notebook, spec=spec, output=tmp_path / "second", timeout=30)

    for result, expected in ((first, "first"), (second, "second")):
        state = open_export(result.path).state("baseline")
        output = cast(
            dict[str, Any],
            decode_json_object(state.output("output").asset_bytes(), "output snapshot"),
        )
        cell = cast(
            dict[str, Any],
            decode_json_object(state.output("cell").asset_bytes(), "cell snapshot"),
        )
        assert state.output("value").json() == expected
        assert expected in output["output"]["data"]
        assert expected in cell["output"]["data"]
        assert cell["console"][0]["data"] == f"{expected}\n"
    first_cell = open_export(first.path).state("baseline").output("cell").asset_bytes()
    second_cell = open_export(second.path).state("baseline").output("cell").asset_bytes()
    assert first_cell != second_cell


def test_value_projection_refreshes_cached_external_ancestor_closure(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    source = tmp_path / "value.txt"
    upstream_counter = tmp_path / "upstream-runs.txt"
    downstream_counter = tmp_path / "downstream-runs.txt"
    _write_external_dependency_notebook(
        notebook,
        source,
        upstream_counter,
        downstream_counter,
    )
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={"value": OutputSpec.json("summary")},
    )
    source.write_text("first", encoding="utf-8")
    build(notebook, spec=spec, output=tmp_path / "first", timeout=30)
    source.write_text("second", encoding="utf-8")
    upstream_counter.write_text("0", encoding="utf-8")
    downstream_counter.write_text("0", encoding="utf-8")

    second = build(notebook, spec=spec, output=tmp_path / "second", timeout=30)

    assert upstream_counter.read_text(encoding="utf-8") == "1"
    assert downstream_counter.read_text(encoding="utf-8") == "1"
    assert open_export(second.path).state("baseline").output("value").json() == "summary=second"
    assert (
        second.cache_activity.authored_hits,
        second.cache_activity.authored_misses,
    ) == (0, 2)


def test_rendered_projection_refreshes_cached_external_ancestor_closure(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    source = tmp_path / "value.txt"
    upstream_counter = tmp_path / "upstream-runs.txt"
    downstream_counter = tmp_path / "downstream-runs.txt"
    _write_external_dependency_notebook(
        notebook,
        source,
        upstream_counter,
        downstream_counter,
    )
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={"output": OutputSpec.output("view")},
    )
    source.write_text("first", encoding="utf-8")
    build(notebook, spec=spec, output=tmp_path / "first", timeout=30)
    source.write_text("second", encoding="utf-8")
    upstream_counter.write_text("0", encoding="utf-8")
    downstream_counter.write_text("0", encoding="utf-8")

    second = build(notebook, spec=spec, output=tmp_path / "second", timeout=30)
    snapshot = cast(
        dict[str, Any],
        decode_json_object(
            open_export(second.path).state("baseline").output("output").asset_bytes(),
            "output snapshot",
        ),
    )

    assert upstream_counter.read_text(encoding="utf-8") == "1"
    assert downstream_counter.read_text(encoding="utf-8") == "1"
    assert "summary=second" in snapshot["output"]["data"]
    assert (
        second.cache_activity.authored_hits,
        second.cache_activity.authored_misses,
    ) == (0, 2)


def test_shared_projection_owner_forces_cached_ancestor_closure_once(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    source = tmp_path / "value.txt"
    upstream_counter = tmp_path / "upstream-runs.txt"
    downstream_counter = tmp_path / "downstream-runs.txt"
    _write_external_dependency_notebook(
        notebook,
        source,
        upstream_counter,
        downstream_counter,
    )
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={
            "value": OutputSpec.json("summary"),
            "output": OutputSpec.output("view"),
            "cell": OutputSpec.cell("report"),
        },
    )
    source.write_text("first", encoding="utf-8")
    build(notebook, spec=spec, output=tmp_path / "first", timeout=30)
    source.write_text("second", encoding="utf-8")
    upstream_counter.write_text("0", encoding="utf-8")
    downstream_counter.write_text("0", encoding="utf-8")

    second = build(notebook, spec=spec, output=tmp_path / "second", timeout=30)
    state = open_export(second.path).state("baseline")
    output = cast(
        dict[str, Any],
        decode_json_object(state.output("output").asset_bytes(), "output snapshot"),
    )
    cell = cast(
        dict[str, Any],
        decode_json_object(state.output("cell").asset_bytes(), "cell snapshot"),
    )

    assert upstream_counter.read_text(encoding="utf-8") == "1"
    assert downstream_counter.read_text(encoding="utf-8") == "1"
    assert state.output("value").json() == "summary=second"
    assert "summary=second" in output["output"]["data"]
    assert "summary=second" in cell["output"]["data"]
    assert cell["console"][0]["data"] == "summary=second\n"
    assert (
        second.cache_activity.authored_hits,
        second.cache_activity.authored_misses,
    ) == (0, 2)


def test_forced_complete_cell_refreshes_stale_descendant_projections(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    source = tmp_path / "value.txt"
    counter = tmp_path / "runs.txt"
    notebook.write_text(
        f"""
import marimo

app = marimo.App()


@app.cell
def source_cell():
    from pathlib import Path
    counter = Path({str(counter)!r})
    runs = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(runs))
    source_value = Path({str(source)!r}).read_text()
    print(source_value)
    return (source_value,)


@app.cell
def downstream(source_value):
    import marimo as mo
    summary = f"summary={{source_value}}"
    view = mo.md(summary)
    return summary, view


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={
            "source": OutputSpec.cell("source_cell"),
            "value": OutputSpec.json("summary"),
            "output": OutputSpec.output("view"),
        },
    )
    source.write_text("first", encoding="utf-8")
    build(notebook, spec=spec, output=tmp_path / "first", timeout=30)
    source.write_text("second", encoding="utf-8")
    counter.write_text("0", encoding="utf-8")
    second = build(notebook, spec=spec, output=tmp_path / "second", timeout=30)

    state = open_export(second.path).state("baseline")
    cell = cast(
        dict[str, Any],
        decode_json_object(state.output("source").asset_bytes(), "cell snapshot"),
    )
    output = cast(
        dict[str, Any],
        decode_json_object(state.output("output").asset_bytes(), "output snapshot"),
    )
    assert counter.read_text(encoding="utf-8") == "1"
    assert cell["console"][0]["data"] == "second\n"
    assert state.output("value").json() == "summary=second"
    assert "summary=second" in output["output"]["data"]
