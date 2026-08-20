from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import marimo_export._marimo.composition as composition
import pytest
from marimo_export._notebook import document_sha256
from marimo_export.errors import ExecutionError
from marimo_export.inspection import inspect_notebook


def _notebook(
    path: Path,
    *,
    first_name: str = "first",
    first_value: int = 1,
    hide_code: bool = False,
    reverse: bool = False,
) -> Path:
    decorator = "@app.cell(hide_code=True)" if hide_code else "@app.cell"
    first = f"""
{decorator}
def {first_name}():
    first_value = {first_value}
    return (first_value,)
"""
    second = """
@app.cell
def second():
    second_value = 2
    return (second_value,)
"""
    cells = second + first if reverse else first + second
    path.write_text(
        (
            "import marimo\n\n"
            "app = marimo.App()\n"
            f"{cells}\n"
            'if __name__ == "__main__":\n'
            "    app.run()\n"
        ),
        encoding="utf-8",
    )
    return path


def test_static_document_digest_matches_managed_inspection(tmp_path: Path) -> None:
    notebook = _notebook(tmp_path / "notebook.py", hide_code=True)

    assert document_sha256(notebook) == inspect_notebook(notebook).document_sha256


def test_document_digest_tracks_code_name_config_and_order(tmp_path: Path) -> None:
    baseline = document_sha256(_notebook(tmp_path / "baseline.py"))
    changed = {
        document_sha256(_notebook(tmp_path / "code.py", first_value=3)),
        document_sha256(_notebook(tmp_path / "name.py", first_name="renamed")),
        document_sha256(_notebook(tmp_path / "config.py", hide_code=True)),
        document_sha256(_notebook(tmp_path / "order.py", reverse=True)),
    }

    assert len(changed) == 4
    assert baseline not in changed


def test_document_digest_parses_without_execution_or_server_start(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        f"""
import marimo

app = marimo.App()


@app.cell
def _():
    from pathlib import Path
    Path({str(marker)!r}).write_text("executed")
    value = 1
    return (value,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    script = """
import sys
from marimo_export._notebook import document_sha256

print(document_sha256(sys.argv[1]))
print("marimo_export._remote.managed" in sys.modules)
"""

    completed = subprocess.run(
        [sys.executable, "-c", script, str(notebook)],
        check=True,
        capture_output=True,
        text=True,
    )

    digest, managed_loaded = completed.stdout.splitlines()
    assert len(digest) == 64
    assert managed_loaded == "False"
    assert not marker.exists()


def test_document_digest_preserves_process_import_context(tmp_path: Path) -> None:
    notebook = _notebook(tmp_path / "notebook.py")
    working_directory = Path.cwd()
    import_path = tuple(sys.path)

    document_sha256(notebook)

    assert Path.cwd() == working_directory
    assert tuple(sys.path) == import_path


def test_document_digest_rejects_a_source_change_during_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = _notebook(tmp_path / "notebook.py")

    def change_source(path: Path, source: bytes) -> str:
        del source
        path.write_text(path.read_text() + "\n# changed\n", encoding="utf-8")
        return "a" * 64

    monkeypatch.setattr(composition, "notebook_document_sha256", change_source)

    with pytest.raises(ExecutionError) as raised:
        document_sha256(notebook)

    assert raised.value.code == "notebook_changed"


def test_document_digest_rejects_change_and_restore_during_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = _notebook(tmp_path / "notebook.py")
    original_source = notebook.read_bytes()
    changed_source = _notebook(tmp_path / "changed.py", first_value=99).read_bytes()
    native_document_sha256 = composition.notebook_document_sha256

    def change_and_restore(path: Path, source: bytes) -> str:
        assert source == original_source
        path.write_bytes(changed_source)
        try:
            return native_document_sha256(path, source)
        finally:
            path.write_bytes(original_source)

    monkeypatch.setattr(composition, "notebook_document_sha256", change_and_restore)

    with pytest.raises(ExecutionError) as raised:
        document_sha256(notebook)

    assert raised.value.code == "notebook_changed"
    assert raised.value.details["before"] == raised.value.details["after"]
    assert raised.value.details["revision_changed"] is True
    assert notebook.read_bytes() == original_source
