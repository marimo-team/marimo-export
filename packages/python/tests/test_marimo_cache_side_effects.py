from __future__ import annotations

from pathlib import Path

from marimo_export import ExportSpec, OutputSpec, build


def test_state_cleanup_writes_no_marimo_cache_export_manifest(tmp_path: Path) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    answer = 42
    return (answer,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )

    build(
        notebook,
        spec=ExportSpec(
            default_state="baseline",
            states={"baseline": {}},
            outputs={"answer": OutputSpec.value("answer")},
        ),
        output=tmp_path / "export",
        timeout=30,
    )

    assert list(tmp_path.rglob(".*-export.json")) == []
