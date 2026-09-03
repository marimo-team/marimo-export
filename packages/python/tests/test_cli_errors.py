from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import marimo_export as sdk
import marimo_export.cli as cli
import pytest
from cli_support import BrokenOutput, spec
from marimo_export.errors import (
    CompatibilityError,
    ExecutionError,
    ExportUnavailableError,
    IntegrityError,
    SessionError,
    SpecError,
    TransportError,
)
from marimo_export.progress import ProgressEvent
from marimo_export.repository import RepositoryError


@pytest.mark.parametrize(
    ("error", "expected_exit"),
    [
        (ValueError("bad input"), cli.EXIT_USAGE),
        (TransportError("offline"), cli.EXIT_ENVIRONMENT),
        (SessionError("missing"), cli.EXIT_ENVIRONMENT),
        (SpecError("bad spec"), cli.EXIT_PLANNING),
        (CompatibilityError("drift"), cli.EXIT_PLANNING),
        (
            ExecutionError("invalid kernel response", code="session_error"),
            cli.EXIT_ENVIRONMENT,
        ),
        (
            ExecutionError("invalid notebook", code="notebook_invalid"),
            cli.EXIT_PLANNING,
        ),
        (ExecutionError("state failed"), cli.EXIT_EXECUTION),
        (IntegrityError("invalid export"), cli.EXIT_INTEGRITY),
        (RepositoryError("repository busy"), cli.EXIT_REPOSITORY),
    ],
)
def test_failures_use_stable_exit_categories(
    error: Exception,
    expected_exit: int,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise error

    monkeypatch.setattr(sdk, "build", fail)

    exit_code = cli.main(
        [
            "build",
            str(tmp_path / "notebook.py"),
            "--spec",
            str(spec(tmp_path / "export.yaml")),
            "--output",
            str(tmp_path / "dist"),
            "--repository",
            str(tmp_path / "repository"),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == expected_exit
    assert payload["ok"] is False
    assert isinstance(payload["error"]["code"], str)


@pytest.mark.parametrize(
    ("command", "machine_flag"),
    [("verify", None), ("verify", "--json"), ("build", "--jsonl")],
)
def test_export_unavailable_uses_the_repository_exit_category(
    command: str,
    machine_flag: str | None,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise ExportUnavailableError(
            "export storage is temporarily unavailable",
            details={"path": "dist/index.json"},
        )

    if command == "verify":
        monkeypatch.setattr(sdk, "verify_export", fail)
        arguments = ["verify", "dist"]
    else:
        monkeypatch.setattr(sdk, "build", fail)
        arguments = [
            "build",
            str(tmp_path / "notebook.py"),
            "--spec",
            str(spec(tmp_path / "export.yaml")),
            "--output",
            str(tmp_path / "dist"),
        ]
    if machine_flag is not None:
        arguments.append(machine_flag)

    assert cli.main(arguments) == cli.EXIT_REPOSITORY
    captured = capsys.readouterr()
    if machine_flag is None:
        assert captured.out == ""
        assert captured.err == "error: export storage is temporarily unavailable\n"
        return
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["error"] == {
        "code": "export_unavailable",
        "details": {"path": "dist/index.json"},
        "message": "export storage is temporarily unavailable",
    }
    assert payload.get("type") == ("error" if machine_flag == "--jsonl" else None)


def test_jsonl_failure_is_terminal_after_progress(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        del args
        progress = kwargs["progress"]
        assert callable(progress)
        callback = cast(Callable[[ProgressEvent], None], progress)
        callback(ProgressEvent(kind="inspection_started"))
        raise ExecutionError("state failed")

    monkeypatch.setattr(sdk, "build", fail)

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
                "--jsonl",
            ]
        )
        == cli.EXIT_EXECUTION
    )
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["type"] for line in lines] == ["progress", "error"]


def test_plan_execution_failures_use_the_planning_exit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise ExecutionError("the notebook baseline failed")

    monkeypatch.setattr(sdk, "plan", fail)

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
        == cli.EXIT_PLANNING
    )
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_tokens_are_redacted_from_messages_and_details(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "top-secret-token"
    monkeypatch.setenv("MARIMO_EXPORT_ACCESS_TOKEN", token)

    def fail(*args: object, **kwargs: object) -> None:
        del args
        progress = kwargs["progress"]
        assert callable(progress)
        callback = cast(Callable[[ProgressEvent], None], progress)
        callback(ProgressEvent(kind="inspection_started", message=f"connecting with {token}"))
        raise TransportError(
            f"server rejected {token}",
            details={"authorization": token},
        )

    monkeypatch.setattr(sdk, "capture", fail)
    exit_code = cli.main(
        [
            "capture",
            "http://127.0.0.1:2718",
            "--session",
            "s_01",
            "--spec",
            str(spec(tmp_path / "export.yaml")),
            "--repository",
            str(tmp_path / "repository"),
            "--output",
            str(tmp_path / "dist"),
            "--jsonl",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == cli.EXIT_ENVIRONMENT
    assert token not in output
    assert [json.loads(line)["type"] for line in output.splitlines()] == ["progress", "error"]
    assert output.count("[REDACTED]") == 3


def test_interrupt_uses_stable_exit_and_json_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupt(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise KeyboardInterrupt

    monkeypatch.setattr(sdk, "build", interrupt)
    exit_code = cli.main(
        [
            "build",
            str(tmp_path / "notebook.py"),
            "--spec",
            str(spec(tmp_path / "export.yaml")),
            "--output",
            str(tmp_path / "dist"),
            "--repository",
            str(tmp_path / "repository"),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == cli.EXIT_INTERRUPT
    assert payload["error"]["code"] == "interrupted"


def test_broken_stdout_pipe_uses_stable_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = BrokenOutput()
    monkeypatch.setattr(sys, "stdout", output)

    assert (
        cli.main(
            [
                "repository",
                "status",
                "--repository",
                str(tmp_path / "repository"),
                "--json",
            ]
        )
        == cli.EXIT_BROKEN_PIPE
    )
    assert output.closed is True
