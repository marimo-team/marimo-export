from __future__ import annotations

from pathlib import Path

from marimo_export import ExportRepository, ExportSpec, OutputSpec, StateSpace, prepare
from marimo_export.index import ControlOptions, ControlRange

NOTEBOOK = """
import marimo

app = marimo.App()


@app.cell
def controls():
    import marimo as mo
    threshold = mo.ui.slider(0, 1, step=0.1, value=0)
    region = mo.ui.dropdown(["north", "south"], value="north")
    mo.hstack([threshold, region])
    return region, threshold


@app.cell
def _(region, threshold):
    summary = {"region": region.value, "threshold": threshold.value}
    return (summary,)


if __name__ == "__main__":
    app.run()
""".lstrip()


def test_prepared_export_records_the_values_each_control_can_send(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(NOTEBOOK, encoding="utf-8")
    spec = ExportSpec.from_state_space(
        StateSpace(
            default_state="matrix-000000",
            matrix={"threshold": [0, 0.2], "region": [["north"], ["south"]]},
        ),
        outputs={"controls": OutputSpec.cell("controls"), "summary": OutputSpec.json("summary")},
    )
    expected = {
        ("threshold", ControlRange(start=0, stop=1, step=0.1)),
        ("region", ControlOptions((["north"], ["south"]))),
    }

    with ExportRepository.open(tmp_path / "repository") as repository:
        with prepare(notebook, spec=spec, repository=repository, timeout=30) as prepared:
            assert not prepared.reused
            bindings = prepared.open().control_bindings.values()
            assert {(binding.input, binding.domain) for binding in bindings} == expected

        with prepare(notebook, spec=spec, repository=repository, timeout=30) as reused:
            assert reused.reused
            bindings = reused.open().control_bindings.values()
            assert {(binding.input, binding.domain) for binding in bindings} == expected


def test_prepared_export_clears_a_domain_that_changes_between_states(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def controls():
    import marimo as mo
    scale = mo.ui.slider(1, 2, value=1)
    scale
    return mo, scale


@app.cell
def choices(mo, scale):
    choice = mo.ui.dropdown(["small"] if scale.value == 1 else ["small", "large"], value="small")
    choice
    return (choice,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    spec = ExportSpec(
        default_state="small",
        states={"small": {}, "large": {"scale": 2, "choice": ["large"]}},
        outputs={"controls": OutputSpec.cell("controls"), "choices": OutputSpec.cell("choices")},
    )

    with (
        ExportRepository.open(tmp_path / "repository") as repository,
        prepare(notebook, spec=spec, repository=repository, timeout=30) as prepared,
    ):
        bindings = prepared.open().control_bindings.values()
        assert {(binding.input, binding.domain) for binding in bindings} == {
            ("scale", ControlRange(start=1, stop=2, step=1)),
            ("choice", None),
        }
