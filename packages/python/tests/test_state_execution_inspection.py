from __future__ import annotations

from pathlib import Path

import pytest
from export_integration_support import native_session as _native_session
from marimo_export import (
    ExportRepository,
    ExportSpec,
    OutputSpec,
    open_export,
)
from marimo_export.errors import ExecutionError
from marimo_export.index import ControlElementStep, ControlIndexStep, ControlKeyStep
from marimo_export.inspection import inspect_notebook


def _markdown_notebook(notebook: Path, title: str = "Prepared notebook") -> None:
    notebook.write_text(
        f"""
import marimo

app = marimo.App()


@app.cell
def controls():
    import marimo as mo
    scale = mo.ui.slider(1, 3, value=1)
    return mo, scale


@app.cell
def introduction(mo):
    mo.md({title!r})
    return


@app.cell
def report(mo, scale):
    answer = scale.value * 2
    mo.md(f"Answer: {{answer}}")
    return (answer,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )


def test_live_capture_preserves_statically_rendered_markdown_cells(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    _markdown_notebook(notebook)
    original = notebook.read_bytes()
    spec = ExportSpec(
        default_state="one",
        states={"one": {"scale": 1}, "three": {"scale": 3}},
        outputs={
            "introduction": OutputSpec.cell("introduction"),
            "answer": OutputSpec.json("answer"),
        },
    )
    with (
        _native_session(notebook) as session,
        ExportRepository.open(tmp_path / "repository") as repository,
    ):
        description = session.inspect()
        assert {cell.name: cell.input_dependencies for cell in description.cells} == {
            "controls": ("scale",),
            "introduction": (),
            "report": ("scale",),
        }
        with session.capture(spec=spec, repository=repository) as prepared:
            assert prepared.plan.document_sha256 == description.document_sha256
            prepared.write(tmp_path / "export")
        assert session.observe_inputs().values == {"scale": 1}
    exported = open_export(tmp_path / "export")
    assert exported.state("one").output("answer").json() == 2
    assert exported.state("three").output("answer").json() == 6
    assert b"Prepared notebook" in exported.state("one").output("introduction").asset_bytes()
    assert notebook.read_bytes() == original


def test_live_document_identity_includes_statically_rendered_markdown(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    _markdown_notebook(notebook, "First title")
    with _native_session(notebook) as session:
        first = session.inspect().document_sha256
    _markdown_notebook(notebook, "Second title")
    with _native_session(notebook) as session:
        second = session.inspect().document_sha256

    assert first != second


def test_live_inspection_rejects_uninitialized_executable_cells(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    _markdown_notebook(notebook)
    with (
        _native_session(notebook, auto_run=False) as session,
        pytest.raises(ExecutionError, match="is not initialized") as raised,
    ):
        session.inspect()

    assert raised.value.code == "parent_document_changed"


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

    assert tuple(definitions["lower"].control_paths.values()) == ((),)
    assert tuple(definitions["upper"].control_paths.values()) == ((),)
    assert len({*definitions["lower"].control_paths, *definitions["upper"].control_paths}) == 2
    assert len(definitions["controls"].control_paths) == 3
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
    assert description.inputs_for({"value": OutputSpec.json("result")}) == (
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
