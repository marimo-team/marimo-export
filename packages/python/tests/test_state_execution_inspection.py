from __future__ import annotations

from pathlib import Path

from marimo_export import (
    OutputSpec,
)
from marimo_export.index import ControlElementStep, ControlIndexStep, ControlKeyStep
from marimo_export.inspection import inspect_notebook


def test_inspection_reports_every_control_in_a_composed_ui_tree(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as mo
    lower = mo.ui.slider(0, 10)
    upper = mo.ui.slider(10, 20)
    controls = mo.ui.array([lower, upper])
    country = mo.ui.text()
    filters = mo.ui.dictionary({"country": country})
    prompt = mo.ui.text()
    submitted = prompt.form()
    return controls, country, filters, lower, prompt, submitted, upper


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )

    description = inspect_notebook(notebook, timeout=30)
    definitions = {definition.name: definition for definition in description.definitions}

    assert len(definitions["lower"].control_paths) == 1
    assert len(definitions["upper"].control_paths) == 1
    assert len(definitions["controls"].control_paths) == 3
    children = {
        *definitions["lower"].control_paths,
        *definitions["upper"].control_paths,
    }
    controls = set(definitions["controls"].control_paths)
    assert len(children) == 2
    assert len(controls) == 3
    assert set(definitions["lower"].control_paths.values()) == {()}
    assert set(definitions["upper"].control_paths.values()) == {()}
    assert set(definitions["controls"].control_paths.values()) == {
        (),
        (ControlIndexStep(value=0),),
        (ControlIndexStep(value=1),),
    }
    assert set(definitions["filters"].control_paths.values()) == {
        (),
        (ControlKeyStep(value="country"),),
    }
    assert set(definitions["submitted"].control_paths.values()) == {
        (),
        (ControlElementStep(),),
    }


def test_inspection_reports_composed_input_dependencies(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as mo
    child = mo.ui.slider(0, 10)
    alias = child
    return alias, child, mo


@app.cell
def _(child, mo):
    parent = mo.ui.array([child])
    return (parent,)


@app.cell
def _(child, mo):
    unrelated_marker = str(child._id)
    unrelated = mo.ui.slider(10, 20)
    return unrelated, unrelated_marker


@app.cell
def _(child, mo):
    second_parent = mo.ui.dictionary({"child": child})
    return (second_parent,)


@app.cell
def report(mo, parent):
    result = len(parent.value)
    view = mo.md(f"count={{result}}")
    return result, view


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )

    description = inspect_notebook(notebook, timeout=30)
    definitions = {definition.name: definition for definition in description.definitions}

    assert definitions["alias"].input_dependencies == ()
    assert definitions["child"].input_dependencies == ()
    assert definitions["parent"].input_dependencies == ("child",)
    assert definitions["second_parent"].input_dependencies == ("child",)
    assert definitions["unrelated"].input_dependencies == ("child",)
    assert description.input_roots() == (
        "alias",
        "parent",
        "second_parent",
        "unrelated",
    )
    assert description.inputs_for({"value": OutputSpec.value("result")}) == (
        "alias",
        "parent",
    )
    assert description.inputs_for({"output": OutputSpec.output("view")}) == (
        "alias",
        "parent",
    )
    assert description.inputs_for({"cell": OutputSpec.cell("report")}) == (
        "alias",
        "parent",
    )
    assert description.inputs_for({"child": OutputSpec.output("child")}) == ("alias",)
    assert description.inputs_for({"alias": OutputSpec.output("alias")}) == ("alias",)
    assert description.inputs_for({"unrelated": OutputSpec.output("unrelated")}) == (
        "alias",
        "unrelated",
    )


def test_inspection_canonicalizes_control_subset_ownership(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as mo
    base = mo.ui.slider(0, 10)
    parent = mo.ui.array([base])
    child_view = parent[0]
    return base, child_view, parent


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )

    description = inspect_notebook(notebook, timeout=30)

    assert description.inputs_for({"child": OutputSpec.output("child_view")}) == ("parent",)
    assert description.inputs_for({"base": OutputSpec.output("base")}) == ("base",)
