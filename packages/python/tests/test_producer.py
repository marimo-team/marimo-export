from __future__ import annotations

from pathlib import Path

import pytest
from marimo_export import ExportSpec, OutputSpec
from marimo_export.descriptors import JsonDescriptor
from marimo_export.errors import ExecutionError, SessionError
from marimo_export.limits import CaptureLimits
from marimo_export.producer import open_notebook
from marimo_export.repository import ExportRepository


def _write_notebook(path: Path, counter: Path) -> None:
    path.write_text(
        f"""\
import marimo

app = marimo.App()


@app.cell
def _():
    from pathlib import Path
    counter = Path({str(counter)!r})
    runs = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(runs))
    answer = 42
    return (answer,)


if __name__ == "__main__":
    app.run()
""",
        encoding="utf-8",
    )


def _spec() -> ExportSpec:
    return ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={"answer": OutputSpec.value("answer")},
    )


def test_owned_notebook_plans_and_captures_after_one_initial_autorun(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    counter = tmp_path / "autoruns.txt"
    _write_notebook(notebook, counter)

    with open_notebook(notebook, timeout=30) as producer:
        description = producer.inspect()
        plan = producer._plan(_spec())
        captured = producer._capture_data(_spec(), CaptureLimits())

    assert counter.read_text(encoding="utf-8") == "1"
    assert description.filename == "notebook.py"
    assert plan["default_alias"] == "baseline"
    assert tuple(captured.index.states) == (captured.index.default_state,)
    descriptor = captured.index.states[captured.index.default_state].outputs["answer"]
    assert isinstance(descriptor, JsonDescriptor)
    assert descriptor.value == 42


def test_owned_notebook_detects_source_change_before_next_operation(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    counter = tmp_path / "autoruns.txt"
    _write_notebook(notebook, counter)

    with open_notebook(notebook, timeout=30) as producer:
        notebook.write_text(
            notebook.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8"
        )
        with pytest.raises(ExecutionError, match="source changed"):
            producer._plan(_spec())


def test_owned_notebook_context_is_single_use(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    _write_notebook(notebook, tmp_path / "autoruns.txt")
    producer = open_notebook(notebook, timeout=30)

    with producer:
        pass
    with pytest.raises(SessionError, match="single use"), producer:
        pass


def test_session_capture_returns_prepared_export_and_borrows_the_session(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    counter = tmp_path / "autoruns.txt"
    _write_notebook(notebook, counter)
    repository = ExportRepository.open(tmp_path / "repository")
    try:
        with open_notebook(notebook, timeout=30) as producer:
            session = producer._require_open()
            plan = session.plan(spec=_spec(), repository=repository)
            assert plan.missing_states == plan.state_fingerprints
            assert counter.read_text(encoding="utf-8") == "1"
            prepared = session.capture(spec=_spec(), repository=repository)
            try:
                assert prepared.open().notebook.filename == "notebook.py"
                assert prepared.open().default_state.output("answer").json() == 42
                assert session.inspect().filename == "notebook.py"
            finally:
                prepared.close()
    finally:
        repository.close()

    assert counter.read_text(encoding="utf-8") == "1"
