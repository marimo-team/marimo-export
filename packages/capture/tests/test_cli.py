from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from click.testing import CliRunner, Result

from moexport.cli import cli


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
""".lstrip(),
        encoding="utf-8",
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
""".lstrip(),
        encoding="utf-8",
    )


def _write_function_notebook(path: Path) -> None:
    path.write_text(
        """
import marimo

__generated_with = "0.23.4"
app = marimo.App()


@app.cell
def _():
    symbol = "AAPL"

    def make_title(value: str) -> str:
        return f"Selected {value}"

    return make_title, symbol


@app.cell
def _(make_title, symbol):
    title = make_title(symbol)
    return (title,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )


def _spec() -> dict[str, Any]:
    return {
        "scenarios": [
            {"id": "base"},
            {"id": "override", "state": {"symbol": "GOOGL"}},
        ],
        "values": {
            "title": {
                "source": {"def": "title"},
                "artifacts": {
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
        "format_id": "text.v1",
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
        "provenance": {"source": "source"},
    }


def _write_spec(path: Path) -> None:
    path.write_text(json.dumps(_spec()), encoding="utf-8")


def _yaml_spec_text() -> str:
    return """
scenarios:
  - id: base
  - id: override
    state:
      symbol: GOOGL
values:
  title:
    source: {def: title}
    artifacts:
      text:
        export:
          type: code
          code: |
            def export(value, ctx, **options):
                blob = ctx.write_blob(
                    "title.txt",
                    str(value).encode(),
                    media_type="text/plain",
                )
                return {
                    "format_id": "text.v1",
                    "media_type": "text/plain",
                    "data": {
                        "type": "bundle",
                        "files": {"value": blob},
                        "entry": "value",
                    },
                    "metadata": {"scenario": ctx.scenario_id},
                }
""".lstrip()


def _write_yaml_spec(path: Path) -> None:
    path.write_text(_yaml_spec_text(), encoding="utf-8")


def _invoke(args: list[str], *, input: str | None = None) -> Result:
    result = CliRunner().invoke(cli, args, input=input)
    assert result.exit_code == 0, result.output
    return result


def _output_json(result: Result) -> Any:
    return json.loads(result.output)


def test_cli_help_exposes_core_workflow_commands() -> None:
    help_text = _invoke(["--help"]).output

    assert {"inspect", "notebook", "query"} <= set(cli.commands)
    assert "marimo-export notebook notebooks/finance.py --spec export.yaml" in help_text
    assert (
        "marimo-export query out entries --value summary --artifact json --content"
        in help_text
    )
    assert "marimo-export query out source --scenario wide_chart" in help_text


def test_cli_inspects_notebook_source_and_defs(tmp_path: Path) -> None:
    notebook = tmp_path / "finance.py"
    _write_notebook(notebook)

    source = _invoke(["inspect", "source", str(notebook)]).output
    assert source == notebook.read_text(encoding="utf-8")

    defs = _output_json(_invoke(["inspect", "defs", str(notebook)]))
    assert defs["notebook"]["name"] == "finance.py"
    assert defs["root_defs"] == ["symbol"]
    assert [item["name"] for item in defs["defs"]] == ["symbol", "title"]
    assert any(
        cell["defs"] == ["title"] and cell["refs"] == ["symbol"]
        for cell in defs["cells"]
    )


def test_cli_exports_notebook_and_reports_query_next_steps(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "finance.py"
    spec = tmp_path / "spec.json"
    bundle_root = tmp_path / "__marimo__" / "static-export"
    _write_notebook(notebook)
    _write_spec(spec)

    payload = _output_json(
        _invoke(
            [
                "notebook",
                str(notebook),
                "--spec",
                str(spec),
                "--to",
                str(bundle_root),
            ]
        )
    )

    assert payload["status"] == "ok"
    assert payload["notebook"]["name"] == "finance.py"
    assert payload["values"] == {
        "title": {
            "source": {"type": "definition", "name": "title"},
            "artifacts": ["text"],
        }
    }
    assert payload["scenarios"] == [
        {
            "id": "base",
            "state": {},
            "values": {"title": ["text"]},
        },
        {
            "id": "override",
            "state": {"symbol": "GOOGL"},
            "values": {"title": ["text"]},
        },
    ]
    assert payload["next"]["catalog"].startswith("marimo-export query ")
    assert " entries " in payload["next"]["entries"]
    assert Path(payload["manifest_path"]).exists()


def test_cli_full_output_is_json_safe_for_python_objects(tmp_path: Path) -> None:
    notebook = tmp_path / "functions.py"
    spec = tmp_path / "spec.json"
    bundle_root = tmp_path / "__marimo__" / "static-export"
    _write_function_notebook(notebook)
    _write_spec(spec)

    payload = _output_json(
        _invoke(
            [
                "notebook",
                str(notebook),
                "--spec",
                str(spec),
                "--to",
                str(bundle_root),
                "--full",
            ]
        )
    )

    defs = payload["evaluation"]["results"][0]["defs"]
    assert payload["bundle_path"]
    assert defs["make_title"]["type"] == "python-object"
    assert defs["make_title"]["python_type"] == "builtins.function"


def test_cli_accepts_yaml_specs_from_file_and_stdin(tmp_path: Path) -> None:
    notebook = tmp_path / "finance.py"
    spec = tmp_path / "spec.yaml"
    bundle_root = tmp_path / "__marimo__" / "static-export"
    _write_notebook(notebook)
    _write_yaml_spec(spec)

    payload = _output_json(
        _invoke(
            [
                "notebook",
                str(notebook),
                "--spec",
                str(spec),
                "--to",
                str(bundle_root),
            ]
        )
    )

    assert payload["status"] == "ok"
    assert [scenario["id"] for scenario in payload["scenarios"]] == [
        "base",
        "override",
    ]


def test_cli_query_progressive_bundle_commands(tmp_path: Path) -> None:
    notebook = tmp_path / "finance.py"
    spec = tmp_path / "spec.json"
    bundle_root = tmp_path / "__marimo__" / "static-export"
    _write_notebook(notebook)
    _write_spec(spec)
    _invoke(
        [
            "notebook",
            str(notebook),
            "--spec",
            str(spec),
            "--to",
            str(bundle_root),
        ]
    )

    catalog = _output_json(_invoke(["query", str(bundle_root)]))
    assert catalog["counts"]["bundles"] == 1
    assert catalog["counts"]["scenarios"] == 2

    scenarios = _output_json(
        _invoke(
            [
                "query",
                str(bundle_root),
                "scenarios",
                "--state",
                "symbol=GOOGL",
            ]
        )
    )
    assert [scenario["id"] for scenario in scenarios] == ["override"]

    artifacts = _output_json(
        _invoke(["query", str(bundle_root), "artifacts", "--value", "title"])
    )
    assert {artifact["scenario"] for artifact in artifacts} == {"base", "override"}

    files = _output_json(
        _invoke(
            [
                "query",
                str(bundle_root),
                "files",
                "--value",
                "title",
                "--media-type",
                "text/plain",
            ]
        )
    )
    assert len(files) == 2
    assert all(Path(file["path"]).exists() for file in files)

    entries = _output_json(
        _invoke(
            [
                "query",
                str(bundle_root),
                "entries",
                "--scenario",
                "override",
                "--value",
                "title",
                "--artifact",
                "text",
                "--content",
            ]
        )
    )
    assert entries[0]["scenario"] == "override"
    assert entries[0]["content"] == {"type": "text", "text": "Selected GOOGL"}

    artifact = _output_json(
        _invoke(
            [
                "query",
                str(bundle_root),
                "artifacts",
                "--one",
                "--scenario",
                "override",
                "--value",
                "title",
                "--artifact",
                "text",
            ]
        )
    )
    assert artifact["scenario"] == "override"
    assert artifact["entry_path"]

    file = _output_json(
        _invoke(
            [
                "query",
                str(bundle_root),
                "files",
                "--one",
                "--scenario",
                "override",
                "--value",
                "title",
                "--artifact",
                "text",
            ]
        )
    )
    assert Path(file["path"]).exists()

    ambiguous = CliRunner().invoke(
        cli,
        ["query", str(bundle_root), "files", "--one", "--value", "title"],
    )
    assert ambiguous.exit_code != 0
    assert "multiple files matched" in ambiguous.output

    ambiguous_entry = CliRunner().invoke(
        cli,
        ["query", str(bundle_root), "entries", "--one", "--value", "title"],
    )
    assert ambiguous_entry.exit_code != 0
    assert "multiple entries matched" in ambiguous_entry.output

    assert _invoke(
        ["query", str(bundle_root), "source", "--scenario", "override"]
    ).output == notebook.read_text(encoding="utf-8")

    source = _output_json(
        _invoke(
            [
                "query",
                str(bundle_root),
                "source",
                "--json",
                "--state",
                "symbol=GOOGL",
            ]
        )
    )
    assert source["name"] == "finance.py"
    assert source["text"] == notebook.read_text(encoding="utf-8")
    assert source["scenarios"] == ["override"]
    assert Path(source["path"]).exists()


def test_cli_accepts_spec_from_stdin(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "finance.py"
    bundle_root = tmp_path / "__marimo__" / "static-export"
    _write_notebook(notebook)
    payload = _output_json(
        _invoke(
            ["notebook", str(notebook), "--spec", "-", "--to", str(bundle_root)],
            input=json.dumps(_spec()),
        )
    )

    assert payload["status"] == "ok"


def test_cli_passes_notebook_args_after_separator(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "finance.py"
    spec = tmp_path / "spec.json"
    bundle_root = tmp_path / "__marimo__" / "static-export"
    _write_cli_args_notebook(notebook)
    _write_spec(spec)

    _invoke(
        [
            "notebook",
            str(notebook),
            "--spec",
            str(spec),
            "--to",
            str(bundle_root),
            "--",
            "--symbol",
            "MSFT",
        ]
    )

    files = _output_json(
        _invoke(
            [
                "query",
                str(bundle_root),
                "files",
                "--scenario",
                "base",
                "--value",
                "title",
            ]
        )
    )

    assert Path(files[0]["path"]).read_text(encoding="utf-8") == "Selected MSFT"
