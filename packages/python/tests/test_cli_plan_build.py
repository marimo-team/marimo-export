from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import marimo_export as sdk
import marimo_export.cli as cli
import pytest
from cli_support import plan, result, spec
from marimo_export.planning import ExportPlan
from marimo_export.progress import ProgressEvent
from marimo_export.repository import ExportRepository
from marimo_export.result import ExportResult


def test_plan_delegates_to_the_public_sdk_and_repository(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = plan()
    recorded: dict[str, object] = {}

    def run(*args: object, **kwargs: object) -> ExportPlan:
        recorded.update(kwargs)
        assert args == (str(tmp_path / "notebook.py"),)
        return resolved

    monkeypatch.setattr(sdk, "plan", run)

    assert (
        cli.main(
            [
                "plan",
                str(tmp_path / "notebook.py"),
                "--spec",
                str(spec(tmp_path / "export.yaml")),
                "--repository",
                str(tmp_path / "repository"),
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["result"] == resolved.to_dict()
    assert isinstance(recorded["repository"], ExportRepository)
    assert recorded["timeout"] == 30.0


def test_plan_prints_the_index_budget(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = plan()
    budget = resolved.index_budget
    monkeypatch.setattr(sdk, "plan", lambda *_args, **_kwargs: resolved)

    assert (
        cli.main(
            [
                "plan",
                str(tmp_path / "notebook.py"),
                "--spec",
                str(spec(tmp_path / "export.yaml")),
                "--repository",
                str(tmp_path / "repository"),
            ]
        )
        == 0
    )

    lines = capsys.readouterr().out.splitlines()
    assert f"Index: at least {budget.index_values:,} of 2,000,000 values, " in "\n".join(lines)
    assert any(line.endswith(" of 16.0 MiB") for line in lines)


@pytest.mark.parametrize("machine_flag", ["--json", "--jsonl"])
def test_build_machine_modes_have_one_terminal_result(
    machine_flag: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_result = result(tmp_path / "dist")

    def run(*args: object, **kwargs: object) -> ExportResult:
        del args
        progress = kwargs["progress"]
        assert callable(progress)
        callback = cast(Callable[[ProgressEvent], None], progress)
        callback(ProgressEvent(kind="inspection_started"))
        callback(ProgressEvent(kind="plan_ready", completed=0, total=1))
        return export_result

    monkeypatch.setattr(sdk, "build", run)
    arguments = [
        "build",
        str(tmp_path / "notebook.py"),
        "--spec",
        str(spec(tmp_path / "export.yaml")),
        "--output",
        str(tmp_path / "dist"),
        "--repository",
        str(tmp_path / "repository"),
        machine_flag,
    ]

    assert cli.main(arguments) == 0

    captured = capsys.readouterr()
    lines = [json.loads(line) for line in captured.out.splitlines()]
    assert captured.err == ""
    if machine_flag == "--json":
        assert len(lines) == 1
        assert lines[0]["ok"] is True
        assert lines[0]["result"] == export_result.to_dict()
    else:
        assert [line["type"] for line in lines] == ["progress", "progress", "result"]
        assert lines[-1]["result"] == export_result.to_dict()


def test_build_human_progress_uses_stderr_and_result_uses_stdout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_result = result(tmp_path / "dist")

    def run(*args: object, **kwargs: object) -> ExportResult:
        del args
        progress = kwargs["progress"]
        assert callable(progress)
        callback = cast(Callable[[ProgressEvent], None], progress)
        callback(ProgressEvent(kind="state_started", completed=0, total=1, state="baseline"))
        return export_result

    monkeypatch.setattr(sdk, "build", run)

    assert (
        cli.main(
            [
                "build",
                str(tmp_path / "notebook.py"),
                "--spec",
                str(spec(tmp_path / "export.yaml")),
                "--output",
                str(tmp_path / "dist"),
                "--repository",
                str(tmp_path / "repository"),
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "Built notebook export" in captured.out
    assert "State started: baseline (0/1)" in captured.err


def test_build_reports_optional_outputs_unavailable_in_every_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_result = replace(result(tmp_path / "dist"), unavailable={"count": 1})
    monkeypatch.setattr(sdk, "build", lambda *args, **kwargs: export_result)
    arguments = [
        "build",
        str(tmp_path / "notebook.py"),
        "--spec",
        str(spec(tmp_path / "export.yaml")),
        "--output",
        str(tmp_path / "dist"),
        "--repository",
        str(tmp_path / "repository"),
    ]

    assert cli.main(arguments) == 0
    captured = capsys.readouterr()
    assert "Unavailable: count in 1 of 1 states" in captured.out
    assert "warning: optional output 'count' is unavailable in every state" in captured.err

    assert cli.main([*arguments, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["unavailable"] == {"count": 1}
