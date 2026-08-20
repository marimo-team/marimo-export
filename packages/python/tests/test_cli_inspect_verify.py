from __future__ import annotations

import json
from types import SimpleNamespace

import marimo_export as sdk
import marimo_export.cli as cli
import marimo_export.inspection as inspection
import marimo_export.sessions as sessions
import pytest
from cli_support import Client, description
from marimo_export.reader import VerificationResult


def test_inspect_is_file_and_live_session_discovery(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EnvironmentClient(Client):
        def __init__(self, *args: object, **kwargs: object) -> None:
            assert set(kwargs) == {"timeout"}
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(
        inspection,
        "inspect_notebook",
        lambda *args, **kwargs: SimpleNamespace(to_dict=description),
    )
    monkeypatch.setattr(sessions, "Client", EnvironmentClient)

    assert cli.main(["inspect", "notebook.py", "--json"]) == 0
    file_result = json.loads(capsys.readouterr().out)
    assert file_result["result"]["definitions"][0]["name"] == "selector"

    assert cli.main(["inspect", "http://127.0.0.1:2718", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["result"]["sessions"] == [
        {"filename": "finance.py", "id": "s_01", "path": "/workspace/finance.py"}
    ]

    assert (
        cli.main(
            [
                "inspect",
                "http://127.0.0.1:2718",
                "--session",
                "s_01",
                "--json",
            ]
        )
        == 0
    )
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["result"]["session_id"] == "s_01"


def test_verify_delegates_to_public_verification(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sdk,
        "verify_export",
        lambda path: VerificationResult(states=2, outputs=4, assets=1, bytes_verified=512),
    )

    assert cli.main(["verify", "dist", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["result"] == {
        "assets": 1,
        "bytes_verified": 512,
        "outputs": 4,
        "states": 2,
    }
