from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from export_integration_support import native_session
from marimo_export import ExportRepository, ExportSpec, OutputSpec, open_export, prepare
from marimo_export.errors import ExecutionError
from marimo_export.index import ControlIndexStep, ControlKeyStep
from marimo_export.progress import ProgressEvent


def test_prepare_replays_nested_batch_controls_between_markdown_cells(tmp_path: Path) -> None:
    notebook = tmp_path / "nested.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def introduction(mo):
    mo.md("Nested controls")
    return


@app.cell
def controls():
    import marimo as mo
    filters = mo.ui.dictionary({
        "panel": mo.md("Bounds: {bounds} Mode: {mode}").batch(
            bounds=mo.ui.array([mo.ui.slider(0, 5, value=1), mo.ui.slider(0, 5, value=2)]),
            mode=mo.ui.dropdown(["sum", "max"], value="sum"),
        )
    })
    filters
    return filters, mo


@app.cell
def explanation(mo):
    mo.md("The controls select one result.")
    return


@app.cell
def report(filters, mo):
    panel = filters.value["panel"]
    result = sum(panel["bounds"]) if panel["mode"] == "sum" else max(panel["bounds"])
    mo.md(f"Result: {result}")
    return result,


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    original = notebook.read_bytes()
    spec = ExportSpec(
        default_state="baseline",
        states={
            "baseline": {},
            "maximum": {"filters": {"panel": {"bounds": {"0": 3, "1": 4}, "mode": ["max"]}}},
        },
        outputs={"controls": OutputSpec.cell("controls"), "result": OutputSpec.json("result")},
    )
    with prepare(notebook, spec=spec, timeout=30) as prepared:
        prepared.write(tmp_path / "export")
    exported = open_export(tmp_path / "export")
    assert exported.state("baseline").output("result").json() == 3
    assert exported.state("maximum").output("result").json() == 4
    assert (
        ControlKeyStep(value="panel"),
        ControlKeyStep(value="bounds"),
        ControlIndexStep(value=1),
    ) in {binding.path for binding in exported.control_bindings.values()}
    assert notebook.read_bytes() == original


def test_live_capture_orders_conditional_controls_after_shared_cell_overrides(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "conditional.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def introduction(mo):
    mo.md("Conditional choices")
    return


@app.cell
def controls():
    import marimo as mo
    factor = 10
    scale = mo.ui.slider(1, 2, value=1)
    return factor, mo, scale


@app.cell
def choices(mo, scale):
    options = ["small"] if scale.value == 1 else ["small", "large"]
    choice = mo.ui.dropdown(options, value="small")
    choice
    return choice,


@app.cell
def result(choice, factor, scale):
    answer = {"factor": factor, "scale": scale.value, "choice": choice.value}
    return answer,


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    original = notebook.read_bytes()
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}, "expanded": {"factor": 100, "scale": 2, "choice": ["large"]}},
        outputs={"answer": OutputSpec.json("answer"), "choices": OutputSpec.cell("choices")},
    )
    with native_session(notebook) as session, ExportRepository.open() as repository:
        before = session.observe_inputs().values
        plan = session.plan(spec=spec, repository=repository)
        observed = session.observe_inputs(plan=plan)
        assert observed.to_value()["values"] == {"factor": 10, "scale": 1, "choice": ["small"]}
        assert {binding.input for binding in observed.control_bindings.values()} == {
            "scale",
            "choice",
        }
        for field in ("document_sha256", "producer_sha256"):
            with pytest.raises(ExecutionError) as stale:
                session.observe_inputs(plan=replace(plan, **{field: "0" * 64}))
            assert stale.value.code == "parent_document_changed"
        with session.capture(spec=spec, repository=repository) as prepared:
            prepared.write(tmp_path / "export")
        assert session.observe_inputs().values == before
        baseline = next(
            state for state in session.plan(spec=spec).states if "baseline" in state.aliases
        )
        assert baseline.inputs["factor"] == 10
    exported = open_export(tmp_path / "export")
    assert exported.state("baseline").output("answer").json() == {
        "factor": 10,
        "scale": 1,
        "choice": "small",
    }
    assert exported.state("expanded").output("answer").json() == {
        "factor": 100,
        "scale": 2,
        "choice": "large",
    }
    assert notebook.read_bytes() == original


def test_live_capture_reuses_last_good_export_after_failure(tmp_path: Path) -> None:
    notebook = tmp_path / "recovery.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def controls():
    import marimo as mo
    def accept(value):
        if value == 3:
            raise ValueError("Three is unavailable")
    scale = mo.ui.slider(1, 3, value=1, on_change=accept)
    return scale,


@app.cell
def result(scale):
    answer = scale.value * 10
    return answer,


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    original = notebook.read_bytes()
    outputs = {"answer": OutputSpec.json("answer")}
    initial = ExportSpec(
        default_state="one", states={"one": {"scale": 1}, "two": {"scale": 2}}, outputs=outputs
    )
    failing = ExportSpec(
        default_state="two",
        states={"two": {"scale": 2}, "three": {"scale": 3}},
        outputs=outputs,
    )
    with native_session(notebook) as session, ExportRepository.open() as repository:
        with session.capture(spec=initial, repository=repository) as first:
            identity = first.identity
        with pytest.raises(ExecutionError) as failure:
            session.capture(spec=failing, repository=repository)
        assert failure.value.code == "input_value_invalid"
        assert session.observe_inputs().values == {"scale": 1}
        with session.capture(spec=initial, repository=repository) as prepared:
            assert prepared.identity == identity
            assert prepared.reused
            assert len(prepared.reused_states) == 2
            assert prepared.prepared_states == ()
            prepared.write(tmp_path / "export")
    exported = open_export(tmp_path / "export")
    assert exported.state("one").output("answer").json() == 10
    assert exported.state("two").output("answer").json() == 20
    assert notebook.read_bytes() == original


def test_live_capture_retries_after_cancellation_with_committed_state_reuse(tmp_path: Path) -> None:
    notebook = tmp_path / "cancelled.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def controls():
    import marimo as mo
    scale = mo.ui.slider(1, 3, value=1)
    return scale,


@app.cell
def result(scale):
    answer = scale.value * 10
    return answer,


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    original = notebook.read_bytes()
    spec = ExportSpec(
        default_state="one",
        states={"one": {"scale": 1}, "two": {"scale": 2}, "three": {"scale": 3}},
        outputs={"answer": OutputSpec.json("answer")},
    )
    cancelled = False
    completed: list[ProgressEvent] = []

    def cancel_after_state(event: ProgressEvent) -> None:
        nonlocal cancelled
        if event.kind == "state_finished":
            completed.append(event)
            cancelled = True

    with native_session(notebook) as session, ExportRepository.open() as repository:
        with pytest.raises(ExecutionError) as failure:
            session.capture(
                spec=spec,
                repository=repository,
                progress=cancel_after_state,
                cancelled=lambda: cancelled,
            )
        assert failure.value.code == "preparation_cancelled"
        assert len(completed) == 1
        assert session.observe_inputs().values == {"scale": 1}
        with session.capture(spec=spec, repository=repository) as prepared:
            assert len(prepared.reused_states) == 1
            assert len(prepared.prepared_states) == 2
            prepared.write(tmp_path / "export")
        assert session.observe_inputs().values == {"scale": 1}
    exported = open_export(tmp_path / "export")
    assert [exported.state(alias).output("answer").json() for alias in ("one", "two", "three")] == [
        10,
        20,
        30,
    ]
    assert notebook.read_bytes() == original


def test_live_capture_reports_a_disappearing_input_and_accepts_a_valid_retry(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "disappearing.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def controls():
    import marimo as mo
    enabled = mo.ui.checkbox(value=True)
    return enabled, mo


@app.cell
def conditional(enabled, mo):
    selector = mo.ui.slider(1, 4, value=2) if enabled.value else None
    selector
    return selector,


@app.cell
def result(selector):
    answer = selector.value if selector is not None else -1
    return answer,


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    original = notebook.read_bytes()
    outputs = {"answer": OutputSpec.json("answer")}
    hidden = ExportSpec(
        default_state="hidden", states={"hidden": {"enabled": False}}, outputs=outputs
    )
    selected = ExportSpec(
        default_state="selected",
        states={"selected": {"enabled": True, "selector": 4}},
        outputs=outputs,
    )
    with native_session(notebook) as session, ExportRepository.open() as repository:
        before = session.observe_inputs().values
        with pytest.raises(ExecutionError) as failure:
            session.capture(spec=hidden, repository=repository)
        assert failure.value.code == "input_value_invalid"
        assert failure.value.details["input"] == "selector"
        assert session.observe_inputs().values == before
        with session.capture(spec=selected, repository=repository) as prepared:
            prepared.write(tmp_path / "export")
        assert session.observe_inputs().values == before
    assert open_export(tmp_path / "export").state("selected").output("answer").json() == 4
    assert notebook.read_bytes() == original
