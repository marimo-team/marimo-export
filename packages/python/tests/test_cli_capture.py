from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import marimo_export as sdk
import marimo_export.cli as cli
import pytest
from cli_support import Prepared, plan, result, spec
from marimo_export.progress import ProgressEvent
from marimo_export.repository import ExportRepository
from marimo_export.result import ExportWarning


def test_capture_prepares_writes_and_reports_export_result(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = plan()
    export_result = result(tmp_path / "dist")
    prepared = Prepared(tmp_path / "repository" / "prepared", resolved, export_result)
    recorded: dict[str, object] = {}

    def run(*args: object, **kwargs: object) -> Prepared:
        recorded.update(kwargs)
        assert args == ("http://127.0.0.1:2718",)
        return prepared

    monkeypatch.setattr(sdk, "capture", run)
    spec_path = spec(tmp_path / "export.yaml")
    repository = tmp_path / "repository"

    assert (
        cli.main(
            [
                "capture",
                "http://127.0.0.1:2718",
                "--session",
                "s_01",
                "--spec",
                str(spec_path),
                "--repository",
                str(repository),
                "--output",
                str(tmp_path / "dist"),
                "--replace",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["result"] == export_result.to_dict()
    assert recorded["session"] == "s_01"
    assert isinstance(recorded["repository"], ExportRepository)
    assert "output" not in recorded
    assert "replace" not in recorded
    assert "access_token" not in recorded
    assert "server_token" not in recorded
    assert prepared.write_calls == [(str(tmp_path / "dist"), True)]
    assert prepared.closed is True


def test_capture_requires_an_output_destination(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit, match=str(cli.EXIT_USAGE)):
        cli.main(
            [
                "capture",
                "http://127.0.0.1:2718",
                "--session",
                "s_01",
                "--spec",
                "export.yaml",
            ]
        )
    assert "--output" in capsys.readouterr().err


def test_capture_preflights_destination_before_remote_preparation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "dist"
    destination.mkdir()
    remote_started = False

    def run(*args: object, **kwargs: object) -> None:
        nonlocal remote_started
        del args, kwargs
        remote_started = True

    monkeypatch.setattr(sdk, "capture", run)

    assert (
        cli.main(
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
                str(destination),
                "--json",
            ]
        )
        == cli.EXIT_REPOSITORY
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "destination_exists"
    assert remote_started is False
    assert not (tmp_path / "repository").exists()


def test_capture_jsonl_orders_preparation_write_and_result(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = plan()
    export_result = result(tmp_path / "dist")
    prepared = Prepared(tmp_path / "repository" / "prepared", resolved, export_result)

    def run(*args: object, **kwargs: object) -> Prepared:
        del args
        progress = kwargs["progress"]
        assert callable(progress)
        callback = cast(Callable[[ProgressEvent], None], progress)
        callback(ProgressEvent(kind="prepared_committed", completed=1, total=1))
        return prepared

    monkeypatch.setattr(sdk, "capture", run)

    assert (
        cli.main(
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
        == 0
    )

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["type"] for line in lines] == ["progress", "progress", "result"]
    assert [line["progress"]["kind"] for line in lines[:-1]] == [
        "prepared_committed",
        "write_finished",
    ]
    assert lines[-1]["result"] == export_result.to_dict()


def test_capture_human_progress_uses_stderr_and_result_uses_stdout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = plan()
    export_result = replace(
        result(tmp_path / "dist"),
        warnings=(
            ExportWarning(
                code="retired_destination_cleanup_failed",
                message="The previous export remains beside the destination.",
                details={"path": str(tmp_path / "retired")},
            ),
        ),
    )
    prepared = Prepared(tmp_path / "repository" / "prepared", resolved, export_result)

    def run(*args: object, **kwargs: object) -> Prepared:
        del args
        progress = kwargs["progress"]
        assert callable(progress)
        callback = cast(Callable[[ProgressEvent], None], progress)
        callback(ProgressEvent(kind="inspection_started"))
        return prepared

    monkeypatch.setattr(sdk, "capture", run)

    assert (
        cli.main(
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
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "Captured notebook export at" in captured.out
    assert "Inspection started" in captured.err
    assert "Write finished (1/1)" in captured.err
    assert "warning: The previous export remains beside the destination." in captured.err
