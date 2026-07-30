from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import marimo_export.cli as cli
import pytest
from marimo_export._writer import write_export
from marimo_export.errors import ExecutionError, TransportError
from marimo_export.export import (
    CacheSummary,
    ExportIndex,
    ExportResult,
    NotebookProvenance,
    PhaseTimings,
    ProducerProvenance,
    Provenance,
    ScalarDescriptor,
    StateEntry,
    StateRunTimings,
    state_fingerprint,
)


def test_root_help_names_five_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="0"):
        cli.main(["--help"])

    output = capsys.readouterr().out
    for command in ("build", "capture", "session", "inspect", "verify"):
        assert command in output


def test_no_command_returns_syntax_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match=str(cli.EXIT_INPUT)):
        cli.main([])

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage:" in captured.err


def test_inspect_emits_human_and_json_summaries(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    export = _export(tmp_path / "export")

    assert cli.main(["inspect", str(export)]) == 0
    human = capsys.readouterr()
    assert "Notebook: finance.py" in human.out
    assert "Representations:" in human.out
    assert "marimo.scalar.v1" in human.out
    assert human.err == ""

    assert cli.main(["inspect", str(export), "--json"]) == 0
    machine = capsys.readouterr()
    result = json.loads(machine.out)
    assert machine.out.count("\n") == 1
    assert machine.err == ""
    assert result["ok"] is True
    assert result["result"]["schema"] == "marimo-export.export.v1"
    assert result["result"]["states"][0]["inputs"] == {"symbol": "AAPL"}
    assert result["result"]["representations"]["count"] == {
        "codec": "marimo.scalar.v1",
        "media_type": "application/vnd.marimo.scalar.v1+json",
    }


def test_verify_reads_the_complete_export(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    export = _export(tmp_path / "export")

    assert cli.main(["verify", str(export), "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "ok": True,
        "result": {
            "assets": 0,
            "bytes_verified": 0,
            "outputs": 2,
            "states": 2,
        },
    }


@pytest.mark.parametrize("command", ["build", "capture"])
def test_export_commands_emit_export_result(
    command: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _result(tmp_path / "exported", mode=command)
    monkeypatch.setattr(cli, command, lambda *args, **kwargs: result)
    spec = _spec(tmp_path / "export.yaml")
    arguments = [command]
    if command == "build":
        arguments.append(str(tmp_path / "notebook.py"))
    else:
        arguments.extend(["http://127.0.0.1:2718", "--session", "s_01"])
    arguments.extend(["--spec", str(spec), "--output", str(tmp_path / "exported"), "--json"])

    assert cli.main(arguments) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result"]["mode"] == command
    assert payload["result"]["states"] == ["baseline", "msft"]
    assert payload["result"]["output_cache"] == {"hits": 2, "misses": 2}
    assert payload["result"]["notebook_cache"] == {"hits": 5, "misses": 1}
    assert payload["result"]["timings"]["state_runs"]["states"] == 2

    arguments.remove("--json")
    assert cli.main(arguments) == 0
    human = capsys.readouterr().out
    assert "Output cache: 2 hits, 2 misses" in human
    assert "Notebook cache: 5 hits, 1 miss" in human
    assert "Phase timings:" in human
    assert "State-run timings (2 states):" in human


def test_session_lists_and_inspects_definitions(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "Client", _FakeClient)

    assert cli.main(["session", "http://127.0.0.1:2718", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["result"]["sessions"] == [
        {"filename": "finance.py", "id": "s_01", "path": "/workspace/finance.py"}
    ]

    assert (
        cli.main(
            [
                "session",
                "http://127.0.0.1:2718",
                "--session",
                "s_01",
                "--json",
            ]
        )
        == 0
    )
    inspected = json.loads(capsys.readouterr().out)
    definition = inspected["result"]["definitions"][0]
    assert definition["name"] == "symbols_selector"
    assert definition["kind"] == "ui"
    assert definition["value"] == ["AAPL", "MSFT"]


@pytest.mark.parametrize(
    ("error", "expected_exit"),
    [
        (ValueError("bad input"), cli.EXIT_INPUT),
        (TransportError("offline"), cli.EXIT_TRANSPORT),
        (ExecutionError("state failed"), cli.EXIT_EXECUTION),
    ],
)
def test_handled_failures_use_stable_json(
    error: Exception,
    expected_exit: int,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise error

    monkeypatch.setattr(cli, "build", fail)
    spec = _spec(tmp_path / "export.yaml")

    exit_code = cli.main(
        [
            "build",
            str(tmp_path / "notebook.py"),
            "--spec",
            str(spec),
            "--output",
            str(tmp_path / "exported"),
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == expected_exit
    assert captured.err == ""
    assert payload["ok"] is False
    assert isinstance(payload["error"]["code"], str)


def test_timeout_validation_is_a_syntax_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match=str(cli.EXIT_INPUT)):
        cli.main(
            [
                "session",
                "http://127.0.0.1:2718",
                "--timeout",
                "nan",
            ]
        )
    assert "positive finite" in capsys.readouterr().err


def _export(path: Path) -> Path:
    provenance = Provenance(
        cache_key="cell_cache/O_count.json",
        return_reference=None,
        python_type="builtins.int",
    )
    index = ExportIndex(
        notebook=NotebookProvenance(filename="finance.py", document_sha256="a" * 64),
        producer=ProducerProvenance(marimo="0.23.15", marimo_export="0.0.0"),
        inputs=("symbol",),
        outputs=("count",),
        states={
            "baseline": StateEntry(
                fingerprint=state_fingerprint({"symbol": "AAPL"}),
                inputs={"symbol": "AAPL"},
                outputs={"count": ScalarDescriptor(value=1, provenance=provenance)},
            ),
            "msft": StateEntry(
                fingerprint=state_fingerprint({"symbol": "MSFT"}),
                inputs={"symbol": "MSFT"},
                outputs={"count": ScalarDescriptor(value=2, provenance=provenance)},
            ),
        },
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    write_export(index, {}, path, replace=False)
    return path


def _spec(path: Path) -> Path:
    path.write_text(
        "schema: marimo-export.spec.v1\n"
        "inputs: [symbol]\n"
        "states:\n"
        "  baseline: {}\n"
        "  msft:\n"
        "    symbol: MSFT\n"
        "outputs:\n"
        "  count:\n"
        "    source: count\n",
        encoding="utf-8",
    )
    return path


def _result(path: Path, *, mode: str) -> ExportResult:
    return ExportResult(
        path=path.absolute(),
        mode=cast(Literal["build", "capture"], mode),
        session_id="s_01" if mode == "capture" else None,
        notebook_filename="finance.py",
        document_sha256="a" * 64,
        producer=ProducerProvenance(marimo="0.23.15", marimo_export="0.0.0"),
        states=("baseline", "msft"),
        outputs=("count", "table"),
        assets=1,
        asset_bytes=2048,
        index_bytes=512,
        output_cache=CacheSummary(hits=2, misses=2),
        notebook_cache=CacheSummary(hits=5, misses=1),
        timings=PhaseTimings(
            total_seconds=3.0,
            server_start_seconds=0.2 if mode == "build" else None,
            initial_autorun_seconds=0.3 if mode == "build" else None,
            capture_seconds=2.0,
            server_shutdown_seconds=0.1 if mode == "build" else None,
            export_write_seconds=0.1,
            state_runs=StateRunTimings(
                states=2,
                setup_seconds=0.2,
                dependency_execution_seconds=0.7,
                ui_update_seconds=0.3,
                output_materialization_seconds=0.6,
                cleanup_seconds=0.2,
            ),
        ),
    )


class _FakeSession:
    id = "s_01"
    filename = "finance.py"
    path = "/workspace/finance.py"

    def inspect(self) -> Any:
        return SimpleNamespace(
            to_dict=lambda: {
                "capabilities": ["cell_cache_receipts"],
                "definitions": [
                    {
                        "cell_id": "cell-1",
                        "domain": {"options": ["AAPL", "MSFT"]},
                        "kind": "ui",
                        "name": "symbols_selector",
                        "portable_input": True,
                        "python_type": "marimo.ui.multiselect",
                        "sensitive": False,
                        "siblings": ["symbols_selector"],
                        "value": ["AAPL", "MSFT"],
                        "value_available": True,
                    }
                ],
                "document_sha256": "a" * 64,
                "filename": self.filename,
                "marimo_export_version": "0.0.0",
                "marimo_version": "0.23.15",
                "path": self.path,
                "session_id": self.id,
            }
        )


class _FakeClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def sessions(self) -> tuple[_FakeSession, ...]:
        return (_FakeSession(),)

    def session(self, session_id: str | None = None) -> _FakeSession:
        assert session_id == "s_01"
        return _FakeSession()
