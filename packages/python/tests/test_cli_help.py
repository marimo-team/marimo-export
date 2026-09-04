from __future__ import annotations

import json

import marimo_export.cli as cli
import pytest


def test_root_help_names_the_command_tree(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit, match="0"):
        cli.main(["--help"])

    output = capsys.readouterr().out
    for command in (
        "plan",
        "build",
        "capture",
        "inspect",
        "verify",
        "observations",
        "repository",
        "doctor",
    ):
        assert command in output
    assert "Prepare and read verified marimo notebook exports." in output
    assert "plan states and reusable work" in output
    assert "list or clear observations" in output
    assert "inspect or prune the export repository" in output
    assert "\n    session " not in output


def test_nested_help_uses_observation_and_export_repository_language(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit, match="0"):
        cli.main(["observations", "--help"])
    assert "{list,clear}" in capsys.readouterr().out

    with pytest.raises(SystemExit, match="0"):
        cli.main(["observations", "list", "--help"])
    observation_help = capsys.readouterr().out
    assert "NOTEBOOK" in observation_help
    assert "--spec FILE" in observation_help
    assert "marimo Python notebook" in observation_help
    assert "export repository" in observation_help
    assert "--producer" not in observation_help

    with pytest.raises(SystemExit, match="0"):
        cli.main(["repository", "--help"])
    repository_help = capsys.readouterr().out
    assert "{status,prune}" in repository_help
    assert "show export repository usage" in repository_help
    assert "apply export repository retention" in repository_help

    with pytest.raises(SystemExit, match="0"):
        cli.main(["doctor", "--help"])
    doctor_help = capsys.readouterr().out
    assert "effective export repository and marimo compatibility" in doctor_help
    assert "export repository" in doctor_help


@pytest.mark.parametrize("command", ["capture", "inspect"])
def test_live_command_help_uses_environment_credentials(
    command: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit, match="0"):
        cli.main([command, "--help"])

    output = capsys.readouterr().out
    assert "MARIMO_EXPORT_ACCESS_TOKEN" in output
    assert "MARIMO_EXPORT_SERVER_TOKEN" in output
    assert "--access-token" not in output
    assert "--server-token" not in output


@pytest.mark.parametrize("option", ["--access-token", "--server-token"])
@pytest.mark.parametrize("form", ["separate", "equals"])
@pytest.mark.parametrize("mode", [None, "--json", "--jsonl"])
def test_live_commands_redact_rejected_argv_credentials(
    option: str,
    form: str,
    mode: str | None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "credential-secret"
    arguments = [
        "capture",
        "http://127.0.0.1:2718",
        "--session",
        "s_01",
        "--spec",
        "export.yaml",
        "--output",
        "dist",
    ]
    if form == "separate":
        arguments.extend((option, secret))
    else:
        arguments.append(f"{option}={secret}")
    if mode is not None:
        arguments.append(mode)

    with pytest.raises(SystemExit, match=str(cli.EXIT_USAGE)):
        cli.main(arguments)

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert secret not in output
    assert "[REDACTED]" in output
    if mode is None:
        assert captured.out == ""
        assert "usage:" in captured.err
    else:
        assert captured.err == ""
        payload = json.loads(captured.out)
        assert payload["error"]["code"] == "invalid_arguments"
        assert payload.get("type") == ("error" if mode == "--jsonl" else None)


def test_no_command_is_a_human_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match=str(cli.EXIT_USAGE)):
        cli.main([])

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage:" in captured.err


@pytest.mark.parametrize("machine_flag", ["--json", "--jsonl"])
def test_parser_failures_honor_machine_mode(
    machine_flag: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit, match=str(cli.EXIT_USAGE)):
        cli.main(["build", "notebook.py", machine_flag])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.err == ""
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_arguments"
    assert payload.get("type") == ("error" if machine_flag == "--jsonl" else None)


def test_build_rejects_json_and_jsonl_together(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit, match=str(cli.EXIT_USAGE)):
        cli.main(
            [
                "build",
                "notebook.py",
                "--spec",
                "export.yaml",
                "--output",
                "dist",
                "--json",
                "--jsonl",
            ]
        )

    payload = json.loads(capsys.readouterr().out)
    assert payload["type"] == "error"


def test_timeout_validation_is_a_machine_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit, match=str(cli.EXIT_USAGE)):
        cli.main(["inspect", "notebook.py", "--timeout", "nan", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert "positive finite" in payload["error"]["message"]
