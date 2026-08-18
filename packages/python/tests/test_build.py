from __future__ import annotations

from pathlib import Path

import marimo_export._build as build_module
import pytest
from marimo_export import ExportSpec, OutputSpec, build, open_export
from marimo_export.client import _CaptureData
from marimo_export.errors import ExecutionError
from marimo_export.export import (
    ExportIndex,
    NotebookProvenance,
    ProducerProvenance,
    Provenance,
    ScalarDescriptor,
    StateEntry,
)
from marimo_export.result import CacheSummary, StateRunTimings


def _spec() -> ExportSpec:
    return ExportSpec(
        inputs=(),
        states={"baseline": {}},
        outputs={"answer": OutputSpec(source="answer")},
    )


def _captured(filename: str) -> _CaptureData:
    return _CaptureData(
        index=ExportIndex(
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
        output_cache=CacheSummary(hits=0, misses=1),
        notebook_cache=CacheSummary(hits=0, misses=1),
        state_run_timings=StateRunTimings(
            states=1,
            setup_seconds=0.1,
            dependency_execution_seconds=0.1,
            ui_update_seconds=0.0,
            output_materialization_seconds=0.1,
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
        output=tmp_path / "export",
    )

    assert result.mode == "build"
    assert result.session_id is None
    assert result.notebook_filename == "notebook.py"
    assert notebook.read_bytes() == source
    assert open_export(result.path).state("baseline").output("answer").scalar() == 42


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
    output = tmp_path / "export"

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
        output=tmp_path / "export",
        timeout=30,
    )

    assert open_export(result.path).state("baseline").output("runtime_file").scalar() == str(
        notebook
    )
    assert notebook.read_bytes() == source


def test_managed_copy_cleanup_has_a_bounded_shutdown_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = tmp_path / ".notebook.marimo-export-copy.py"
    notebook.write_text("import marimo\n", encoding="utf-8")

    def fail_unlink(path: Path) -> None:
        del path
        raise PermissionError("private path")

    monkeypatch.setattr(Path, "unlink", fail_unlink)

    with pytest.raises(ExecutionError) as raised:
        build_module._remove_working_notebook(notebook)

    assert raised.value.code == "server_shutdown_failed"
    assert str(raised.value) == "the managed notebook copy could not be removed"
    assert raised.value.details == {"exception_type": "PermissionError"}


def test_managed_copy_creation_removes_partial_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text("import marimo\n", encoding="utf-8")

    def fail_fsync(descriptor: int) -> None:
        del descriptor
        raise OSError("sync failed")

    monkeypatch.setattr(build_module.os, "fsync", fail_fsync)

    with pytest.raises(ExecutionError) as raised:
        build_module._copy_notebook(notebook)

    assert raised.value.code == "server_start_failed"
    assert raised.value.details == {"exception_type": "OSError"}
    assert not tuple(tmp_path.glob(".notebook.marimo-export-*.py"))


def test_managed_copy_creation_removes_snapshot_on_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text("import marimo\n", encoding="utf-8")

    def cancel_fsync(descriptor: int) -> None:
        del descriptor
        raise KeyboardInterrupt("cancelled")

    monkeypatch.setattr(build_module.os, "fsync", cancel_fsync)

    with pytest.raises(KeyboardInterrupt, match="cancelled"):
        build_module._copy_notebook(notebook)

    assert not tuple(tmp_path.glob(".notebook.marimo-export-*.py"))


@pytest.mark.parametrize("timeout", [0, float("nan")])
def test_build_rejects_invalid_timeout(tmp_path: Path, timeout: float) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text("import marimo\n", encoding="utf-8")

    with pytest.raises(ValueError, match="positive finite"):
        build(
            notebook,
            spec=_spec(),
            output=tmp_path / "export",
            timeout=timeout,
        )
