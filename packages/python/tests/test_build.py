from __future__ import annotations

from pathlib import Path

import pytest
from marimo_export import ExportSpec, OutputSpec
from marimo_export._build import build
from marimo_export.errors import ExecutionError
from marimo_export.reader import open_export
from marimo_export.repository import ExportRepository


def _spec() -> ExportSpec:
    return ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={"answer": OutputSpec.json("answer")},
    )


def _notebook(path: Path, counter: Path | None = None) -> None:
    counter_code = ""
    if counter is not None:
        counter_code = f"""
    counter = Path({str(counter)!r})
    runs = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(runs))
"""
    path.write_text(
        f"""\
import marimo

app = marimo.App()


@app.cell
def _():
    from pathlib import Path
{counter_code}
    answer = 42
    return (answer,)


if __name__ == "__main__":
    app.run()
""",
        encoding="utf-8",
    )


def test_build_preflights_destination_before_preparation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    expected = object()

    class Prepared:
        def __enter__(self):
            events.append("enter")
            return self

        def __exit__(self, *_error: object) -> None:
            events.append("close")

        def write(self, *_args, **_kwargs):
            events.append("write")
            return expected

    monkeypatch.setattr(
        "marimo_export._build.preflight_export_destination",
        lambda *_args, **_kwargs: events.append("preflight"),
    )
    monkeypatch.setattr(
        "marimo_export._build.prepare",
        lambda *_args, **_kwargs: (events.append("prepare"), Prepared())[1],
    )

    result = build(tmp_path / "notebook.py", spec=_spec(), output=tmp_path / "dist")

    assert result is expected
    assert events == ["preflight", "prepare", "enter", "write", "close"]


def test_build_prepares_once_then_reuses_without_starting_a_notebook(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    counter = tmp_path / "autoruns.txt"
    _notebook(notebook, counter)
    source = notebook.read_bytes()
    repository = ExportRepository.open(tmp_path / "repository")
    try:
        first = build(
            notebook,
            spec=_spec(),
            output=tmp_path / "first",
            repository=repository,
            timeout=30,
        )
        second = build(
            notebook,
            spec=_spec(),
            output=tmp_path / "second",
            repository=repository,
            timeout=30,
        )
    finally:
        repository.close()

    assert counter.read_text(encoding="utf-8") == "1"
    assert notebook.read_bytes() == source
    assert first.reused is False
    assert second.reused is True
    assert first.identity == second.identity
    assert open_export(first.path).default_state.output("answer").json() == 42
    assert open_export(second.path).default_state.output("answer").json() == 42


def test_source_change_after_identity_preflight_fails_before_publish(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    _notebook(notebook)
    from marimo_export.producer import open_notebook

    def changed(path: Path, *, timeout: float):
        path.write_text(path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
        return open_notebook(path, timeout=timeout)

    monkeypatch.setattr("marimo_export._services.prepare_export.open_notebook", changed)

    with pytest.raises(ExecutionError):
        build(notebook, spec=_spec(), output=tmp_path / "dist", timeout=30)

    assert not (tmp_path / "dist").exists()


@pytest.mark.parametrize("timeout", [0, float("nan")])
def test_build_rejects_invalid_timeout_before_source_inspection(
    tmp_path: Path,
    timeout: float,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text("not a notebook\n", encoding="utf-8")

    with pytest.raises(ValueError, match="positive finite"):
        build(notebook, spec=_spec(), output=tmp_path / "dist", timeout=timeout)
