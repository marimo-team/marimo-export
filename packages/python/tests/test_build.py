from __future__ import annotations

from pathlib import Path

import marimo_export._build as build_module
import pytest
from marimo_export import ExportSpec, OutputSpec, build, open_publication
from marimo_export.client import _CaptureData
from marimo_export.errors import ExecutionError
from marimo_export.publication import (
    CacheSummary,
    FreshChildTimings,
    NotebookProvenance,
    ProducerProvenance,
    Provenance,
    PublicationIndex,
    ScalarDescriptor,
    StateEntry,
)


def _spec() -> ExportSpec:
    return ExportSpec(
        inputs=(),
        states={"baseline": {}},
        outputs={"answer": OutputSpec(source="answer")},
    )


def _captured(filename: str) -> _CaptureData:
    return _CaptureData(
        index=PublicationIndex(
            notebook=NotebookProvenance(
                filename=filename,
                document_sha256="a" * 64,
            ),
            producer=ProducerProvenance(
                marimo="0.23.15",
                marimo_export="1.0.0",
            ),
            inputs=(),
            outputs=("answer",),
            states={
                "baseline": StateEntry(
                    inputs={},
                    outputs={
                        "answer": ScalarDescriptor(
                            value=42,
                            provenance=Provenance(
                                cache_key="cell_cache/answer.json",
                                return_reference=None,
                                python_type="builtins.int",
                            ),
                        )
                    },
                )
            },
        ),
        assets={},
        projection_cache=CacheSummary(hits=0, misses=1),
        upstream_cache=CacheSummary(hits=0, misses=1),
        fresh_child_timings=FreshChildTimings(
            states=1,
            construction_seconds=0.1,
            upstream_execution_seconds=0.1,
            ui_application_seconds=0.0,
            projection_execution_seconds=0.1,
            cleanup_seconds=0.1,
        ),
        capture_seconds=0.4,
        server_start_seconds=0.1,
        initial_autorun_seconds=0.1,
        server_shutdown_seconds=0.1,
    )


def test_build_commits_owned_capture_after_source_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text("import marimo\n", encoding="utf-8")
    source = notebook.read_bytes()
    monkeypatch.setattr(
        build_module,
        "_capture_owned",
        lambda path, spec, timeout: _captured(path.name),
    )

    result = build(
        notebook,
        spec=_spec(),
        output=tmp_path / "publication",
    )

    assert result.mode == "build"
    assert result.session_id is None
    assert result.notebook_filename == "notebook.py"
    assert notebook.read_bytes() == source
    assert open_publication(result.path).state("baseline").output("answer").scalar() == 42


def test_build_rejects_source_change_before_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text("import marimo\n", encoding="utf-8")

    def change_source(
        path: Path,
        spec: ExportSpec,
        timeout: float,
    ) -> _CaptureData:
        del spec, timeout
        path.write_text("import marimo\n# changed\n", encoding="utf-8")
        return _captured(path.name)

    monkeypatch.setattr(build_module, "_capture_owned", change_source)
    output = tmp_path / "publication"

    with pytest.raises(ExecutionError) as raised:
        build(notebook, spec=_spec(), output=output)

    assert raised.value.code == "notebook_changed"
    assert not output.exists()


def test_build_preserves_the_authored_runtime_filename(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    runtime_file = __file__
    return (runtime_file,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    source = notebook.read_bytes()

    result = build(
        notebook,
        spec=ExportSpec(
            inputs=(),
            states={"baseline": {}},
            outputs={"runtime_file": OutputSpec(source="runtime_file")},
        ),
        output=tmp_path / "publication",
        timeout=30,
    )

    assert open_publication(result.path).state("baseline").output("runtime_file").scalar() == str(
        notebook
    )
    assert notebook.read_bytes() == source


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_build_rejects_invalid_timeout(tmp_path: Path, timeout: float) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text("import marimo\n", encoding="utf-8")

    with pytest.raises(ValueError, match="positive finite"):
        build(
            notebook,
            spec=_spec(),
            output=tmp_path / "publication",
            timeout=timeout,
        )
