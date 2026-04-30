from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast

import pytest

from moexport.notebook import export_notebook


def _write_notebook(path: Path) -> None:
    path.write_text(
        """
import marimo

__generated_with = "0.23.4"
app = marimo.App()


@app.cell
def _():
    symbol = "AAPL"
    return (symbol,)


@app.cell
def _(symbol):
    title = f"Selected {symbol}"
    return (title,)


if __name__ == "__main__":
    app.run()
""".lstrip()
    )


def _write_cli_args_notebook(path: Path) -> None:
    path.write_text(
        """
import marimo

__generated_with = "0.23.4"
app = marimo.App()


@app.cell
def _():
    import marimo as mo
    symbol = mo.cli_args().get("symbol", "AAPL")
    return (symbol,)


@app.cell
def _(symbol):
    title = f"Selected {symbol}"
    return (title,)


if __name__ == "__main__":
    app.run()
""".lstrip()
    )


def _write_side_effect_notebook(path: Path, log_path: Path) -> None:
    path.write_text(
        f"""
import marimo

__generated_with = "0.23.4"
app = marimo.App()


@app.cell
def _():
    from pathlib import Path
    symbols = ["AAPL", "CRWV", "MSFT", "GOOGL", "AMZN"]
    log_path = {str(log_path)!r}
    return Path, log_path, symbols


@app.cell
def _(Path, log_path, symbols):
    path = Path(log_path)
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(previous + ",".join(symbols) + "\\n", encoding="utf-8")
    selected = ",".join(symbols)
    return (selected,)


if __name__ == "__main__":
    app.run()
""".lstrip()
    )


def _text_export_spec() -> dict[str, Any]:
    return {
        "scenarios": [{"id": "base"}],
        "values": {
            "title": {
                "source": "title",
                "formats": {
                    "text": {
                        "export": {
                            "type": "code",
                            "code": """
def export(value, ctx, **options):
    blob = ctx.write_blob(
        "title.txt",
        str(value).encode(),
        media_type="text/plain",
    )
    return {
        "format": "text.v1",
        "media_type": "text/plain",
        "data": {
            "type": "bundle",
            "files": {"value": blob},
            "entry": "value",
        },
        "metadata": {"scenario": ctx.scenario_id},
    }
""",
                        }
                    }
                },
            }
        },
    }


def _selected_export_spec() -> dict[str, Any]:
    spec = _text_export_spec()
    spec["scenarios"] = [
        {"id": "subset", "state": {"symbols": ["CRWV", "MSFT"]}},
    ]
    spec["values"] = {
        "selected": {
            "source": "selected",
            "formats": spec["values"]["title"]["formats"],
        }
    }
    return spec


def test_export_notebook_loads_file_and_writes_bundle(tmp_path: Path) -> None:
    notebook = tmp_path / "finance.py"
    _write_notebook(notebook)

    result = export_notebook(
        notebook,
        {
            "scenarios": [
                {"id": "base"},
                {"id": "override", "state": {"symbol": "GOOGL"}},
            ],
            "values": {
                "title": {
                    "source": "title",
                    "formats": {
                        "text": {
                            "export": {
                                "type": "code",
                                "code": """
def export(value, ctx, **options):
    blob = ctx.write_blob(
        "title.txt",
        str(value).encode(),
        media_type="text/plain",
    )
    return {
        "format": "text.v1",
        "media_type": "text/plain",
        "data": {
            "type": "bundle",
            "files": {"value": blob},
            "entry": "value",
        },
        "metadata": {"scenario": ctx.scenario_id},
    }
""",
                            }
                        }
                    },
                }
            },
        },
        bundle=tmp_path / "export",
    )

    manifest = result.manifest
    output_root = Path(result.bundle_path).parent.parent
    base_blob = manifest["scenarios"][0]["values"]["title"]["text"]["data"]["files"][
        "value"
    ]
    override_blob = manifest["scenarios"][1]["values"]["title"]["text"]["data"][
        "files"
    ]["value"]
    source_blob = manifest["notebook"]["source"]

    assert manifest["notebook"]["name"] == "finance.py"
    assert (
        source_blob["sha256"]
        == hashlib.sha256(
            notebook.read_bytes(),
        ).hexdigest()
    )
    assert source_blob["media_type"] == "text/x-python"
    assert (output_root / source_blob["href"]).read_text() == notebook.read_text()
    assert manifest["values"]["title"] == {"source": "title", "formats": ["text"]}
    assert (output_root / base_blob["href"]).read_text() == "Selected AAPL"
    assert (output_root / override_blob["href"]).read_text() == "Selected GOOGL"


def test_export_notebook_passes_notebook_args(tmp_path: Path) -> None:
    notebook = tmp_path / "cli_args.py"
    _write_cli_args_notebook(notebook)

    result = export_notebook(
        notebook,
        _text_export_spec(),
        bundle=tmp_path / "export",
        run={"args": ["--symbol", "MSFT"]},
    )

    artifact = result.manifest["scenarios"][0]["values"]["title"]["text"]
    blob = artifact["data"]["files"]["value"]

    assert (Path(result.bundle_path).parent.parent / blob["href"]).read_text() == (
        "Selected MSFT"
    )


def test_export_notebook_does_not_run_default_notebook_before_scenarios(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "finance.py"
    log_path = tmp_path / "symbols.log"
    _write_side_effect_notebook(notebook, log_path)

    result = export_notebook(
        notebook,
        _selected_export_spec(),
        bundle=tmp_path / "export",
    )

    artifact = result.manifest["scenarios"][0]["values"]["selected"]["text"]
    blob = artifact["data"]["files"]["value"]

    assert log_path.read_text(encoding="utf-8").splitlines() == ["CRWV,MSFT"]
    assert (Path(result.bundle_path).parent.parent / blob["href"]).read_text() == (
        "CRWV,MSFT"
    )


def test_export_notebook_source_can_use_mox_runtime_cell_output(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "finance.py"
    notebook.write_text(
        """
import marimo

__generated_with = "0.23.4"
app = marimo.App()


@app.cell
def _():
    symbol = "AAPL"
    return (symbol,)


@app.cell
def _(symbol):
    f"Selected {symbol}"


if __name__ == "__main__":
    app.run()
""".lstrip()
    )
    spec = _text_export_spec()
    spec["scenarios"] = [{"id": "override", "state": {"symbol": "MSFT"}}]
    spec["values"]["title"]["source"] = "mox.runtime().cell(index=1).output"

    result = export_notebook(
        notebook,
        spec,
        bundle=tmp_path / "export",
    )

    artifact = result.manifest["scenarios"][0]["values"]["title"]["text"]
    blob = artifact["data"]["files"]["value"]

    assert (Path(result.bundle_path).parent.parent / blob["href"]).read_text() == (
        "Selected MSFT"
    )


def test_export_notebook_uses_marimo_name_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = tmp_path / "remote.py"
    _write_notebook(notebook)
    calls: list[tuple[str, bool, bool]] = []

    def fake_validate_name(
        name: str,
        allow_new_file: bool,
        allow_directory: bool,
    ) -> tuple[str, None]:
        calls.append((name, allow_new_file, allow_directory))
        return str(notebook), None

    monkeypatch.setattr("moexport.notebook.validate_name", fake_validate_name)

    result = export_notebook(
        "https://example.com/notebook.py",
        _text_export_spec(),
        bundle=tmp_path / "export",
    )

    assert calls == [("https://example.com/notebook.py", False, False)]
    assert result.manifest["notebook"]["name"] == "remote.py"


def test_export_notebook_rejects_unknown_run_options(tmp_path: Path) -> None:
    notebook = tmp_path / "finance.py"
    _write_notebook(notebook)

    with pytest.raises(TypeError, match="mode"):
        export_notebook(
            notebook,
            _text_export_spec(),
            bundle=tmp_path / "export",
            run=cast(Any, {"mode": "unsupported"}),
        )
