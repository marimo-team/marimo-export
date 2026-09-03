from __future__ import annotations

import json
from pathlib import Path

import marimo_export as sdk
import marimo_export.cli as cli
import marimo_export.diagnostics as diagnostics
import pytest
from cli_support import observed_plan, spec
from marimo_export.diagnostics import CheckResult
from marimo_export.planning import ExportPlan
from marimo_export.repository import ExportRepository, PruneResult


def test_observations_list_renders_the_public_export_plan(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = observed_plan()
    repository = tmp_path / "repository"
    calls: list[str] = []

    def run(notebook: str, **kwargs: object) -> ExportPlan:
        calls.append(notebook)
        assert isinstance(kwargs["repository"], ExportRepository)
        return resolved

    monkeypatch.setattr(sdk, "plan", run)
    spec_path = spec(tmp_path / "export.yaml")
    arguments = [
        str(tmp_path / "notebook.py"),
        "--spec",
        str(spec_path),
        "--repository",
        str(repository),
    ]

    assert cli.main(["observations", "list", *arguments, "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)["result"]
    assert listed["producer_sha256"] == resolved.producer_sha256
    assert listed["observation_revision"] == 1
    assert listed["inputs"] == ["selector"]
    assert listed["observations"][0]["values"] == {"selector": "MSFT"}

    assert cli.main(["observations", "list", *arguments]) == 0
    human = capsys.readouterr().out
    assert "Inputs: selector" in human
    assert "Observation revision: 1" in human
    assert '{"selector": "MSFT"}' in human
    assert calls == [str(tmp_path / "notebook.py")] * 2


def test_observations_clear_resolves_the_public_export_plan(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = observed_plan()
    repository = tmp_path / "repository"
    with ExportRepository.open(repository) as opened:
        opened.record_observation(resolved, {"selector": "MSFT"})

    def run(notebook: str, **kwargs: object) -> ExportPlan:
        assert notebook == str(tmp_path / "notebook.py")
        assert isinstance(kwargs["repository"], ExportRepository)
        return resolved

    monkeypatch.setattr(sdk, "plan", run)
    assert (
        cli.main(
            [
                "observations",
                "clear",
                str(tmp_path / "notebook.py"),
                "--spec",
                str(spec(tmp_path / "export.yaml")),
                "--repository",
                str(repository),
                "--json",
            ]
        )
        == 0
    )
    cleared = json.loads(capsys.readouterr().out)["result"]
    assert cleared == {
        "cleared": 1,
        "observation_revision": 1,
        "producer_sha256": resolved.producer_sha256,
    }
    with ExportRepository.open(repository) as opened:
        assert opened.observations(resolved) == ()


def test_repository_flag_precedes_environment_and_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = tmp_path / "environment"
    explicit = tmp_path / "explicit"
    monkeypatch.setenv("MARIMO_EXPORT_REPOSITORY", str(environment))

    assert cli.main(["repository", "status", "--json"]) == 0
    from_environment = json.loads(capsys.readouterr().out)["result"]
    assert from_environment["path"] == str(environment.resolve())

    assert (
        cli.main(
            [
                "repository",
                "status",
                "--repository",
                str(explicit),
                "--json",
            ]
        )
        == 0
    )
    from_flag = json.loads(capsys.readouterr().out)["result"]
    assert from_flag["path"] == str(explicit.resolve())


def test_repository_prune_supports_dry_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []
    original = ExportRepository.prune

    def tracked(repository: ExportRepository, *, dry_run: bool = False) -> PruneResult:
        calls.append(dry_run)
        return original(repository, dry_run=dry_run)

    monkeypatch.setattr(ExportRepository, "prune", tracked)

    assert (
        cli.main(
            [
                "repository",
                "prune",
                "--repository",
                str(tmp_path / "repository"),
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)["result"]
    assert result["dry_run"] is True
    assert result["prepared_states"] == 0
    assert result["generations"] == 0
    assert calls == [True]


def test_doctor_reports_effective_repository_and_exact_marimo_check(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        diagnostics,
        "marimo_compatibility",
        lambda: CheckResult(
            name="marimo",
            status="pass",
            message="Marimo 0.24.0 matches the supported adapter.",
            details={
                "adapter": "private-marimo-0.24.0",
                "release_commit": "8" * 40,
                "version": "0.24.0",
            },
        ),
    )
    repository = tmp_path / "repository"

    assert (
        cli.main(
            [
                "doctor",
                "--repository",
                str(repository),
                "--json",
            ]
        )
        == 0
    )

    result = json.loads(capsys.readouterr().out)["result"]
    assert result["ok"] is True
    assert result["repository"]["path"] == str(repository.resolve())
    assert result["marimo"] == {
        "details": {
            "adapter": "private-marimo-0.24.0",
            "release_commit": "8" * 40,
            "version": "0.24.0",
        },
        "message": "Marimo 0.24.0 matches the supported adapter.",
        "name": "marimo",
        "status": "pass",
    }


def test_doctor_returns_planning_exit_when_marimo_is_incompatible(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        diagnostics,
        "marimo_compatibility",
        lambda: CheckResult(
            name="marimo",
            status="fail",
            message="The installed Marimo adapter contract differs.",
            details={"code": "marimo_incompatible"},
        ),
    )

    assert (
        cli.main(
            [
                "doctor",
                "--repository",
                str(tmp_path / "repository"),
                "--json",
            ]
        )
        == cli.EXIT_PLANNING
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["result"]["marimo"]["status"] == "fail"
