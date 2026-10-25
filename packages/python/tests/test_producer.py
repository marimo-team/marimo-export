from __future__ import annotations

from pathlib import Path

import pytest
from export_integration_support import NOTEBOOK_TIMEOUT
from marimo._config.manager import get_default_config_manager
from marimo._environments import script_metadata
from marimo._runtime.callbacks.cache import cache_cells_enabled
from marimo_export import ExportSpec, OutputSpec
from marimo_export._notebook import _read_stable_source, document_sha256
from marimo_export.errors import ExecutionError, SessionError
from marimo_export.producer import _copy_notebook, open_notebook
from marimo_export.repository import ExportRepository

_CELLS = """\
import marimo

app = marimo.App()


@app.cell
def _():
    answer = 42
    return (answer,)


if __name__ == "__main__":
    app.run()
"""


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
        outputs={"answer": OutputSpec.json("answer")},
    )


def test_owned_notebook_detects_source_change_before_next_operation(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    counter = tmp_path / "autoruns.txt"
    _write_notebook(notebook, counter)

    with open_notebook(notebook, timeout=NOTEBOOK_TIMEOUT) as producer:
        notebook.write_text(
            notebook.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8"
        )
        with pytest.raises(ExecutionError, match="source changed"):
            producer._plan(_spec())


def test_owned_notebook_context_is_single_use(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    _write_notebook(notebook, tmp_path / "autoruns.txt")
    producer = open_notebook(notebook, timeout=NOTEBOOK_TIMEOUT)

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
        with open_notebook(notebook, timeout=NOTEBOOK_TIMEOUT) as producer:
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


def _managed_copy(notebook: Path) -> Path:
    _, sha256, revision = _read_stable_source(notebook)
    return _copy_notebook(notebook, expected_sha256=sha256, expected_revision=revision)


@pytest.mark.parametrize(
    "authored",
    [
        pytest.param(_CELLS, id="no-block"),
        pytest.param(
            "# /// script\n"
            '# requires-python = ">=3.10"\n'
            "# dependencies = [\n"
            '#     "marimo",\n'
            '#     "polars>=1",\n'
            "# ]\n"
            "#\n"
            "# [tool.marimo.runtime]\n"
            "# cache_cells = false\n"
            '# auto_reload = "lazy"\n'
            "#\n"
            "# [tool.marimo.display]\n"
            '# theme = "dark"\n'
            "# ///\n"
            "\n" + _CELLS,
            id="existing-block",
        ),
        pytest.param(
            "# /// script\r\n"
            '# requires-python = ">=3.11"\r\n'
            "# ///\r\n"
            "\r\n" + _CELLS.replace("\n", "\r\n"),
            id="crlf",
        ),
        pytest.param("#!/usr/bin/env python\n" + _CELLS, id="shebang"),
    ],
)
def test_managed_copy_enables_cell_cache_and_keeps_the_document(
    tmp_path: Path,
    authored: str,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.marimo.runtime]\ncache_cells = false\n",
        encoding="utf-8",
    )
    notebook = tmp_path / "notebook.py"
    notebook.write_bytes(authored.encode("utf-8"))

    copy = _managed_copy(notebook)

    assert notebook.read_bytes() == authored.encode("utf-8")
    assert not cache_cells_enabled(
        get_default_config_manager(current_path=str(notebook)).get_config()
    )
    assert cache_cells_enabled(get_default_config_manager(current_path=str(copy)).get_config())
    copied = copy.read_bytes().decode("utf-8")
    expected = script_metadata.loads(authored) or {}
    expected.setdefault("tool", {}).setdefault("marimo", {}).setdefault("runtime", {})[
        "cache_cells"
    ] = True
    assert script_metadata.loads(copied) == expected
    newline = "\r\n" if "\r\n" in authored else "\n"
    assert copied.replace("\r\n", "\n").replace("\n", newline) == copied
    if authored.startswith("#!"):
        assert copied.splitlines()[0] == authored.splitlines()[0]
    assert document_sha256(copy) == document_sha256(notebook)


@pytest.mark.parametrize(
    "header",
    [
        "# /// script\n# dependencies = [\n# ///\n",
        "# /// script\n# tool = 1\n# ///\n",
        "# /// script\n# x = 1\n# ///\n# /// script\n# y = 1\n# ///\n",
    ],
    ids=["invalid-toml", "tool-not-a-table", "two-blocks"],
)
def test_managed_copy_rejects_unusable_script_metadata(tmp_path: Path, header: str) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(header + _CELLS, encoding="utf-8")

    with pytest.raises(ExecutionError) as raised:
        _managed_copy(notebook)

    assert raised.value.code == "notebook_invalid"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["notebook.py"]
