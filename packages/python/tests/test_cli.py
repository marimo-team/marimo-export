from __future__ import annotations

import hashlib
import json
from importlib.metadata import version
from pathlib import Path
from typing import cast

import marimo_export.cli as cli
import msgspec
import pytest
from marimo_export.errors import CaptureError, SessionError, TransportError
from marimo_export.spec import ExportSpec


def _publication(
    root: Path,
    *,
    data: bytes = b'{"answer":42}',
    format_name: str = "json",
    format_id: str = "json.v1",
    media_type: str = "application/json",
) -> Path:
    envelope = msgspec.msgpack.encode(
        {
            "data": data,
            "media_type": media_type,
            "filename": None,
            "metadata": {"format_id": format_id, "metadata_json": b"{}"},
        }
    )
    key = "project/hash/return.bin"
    index = {
        "schema": "marimo-export.publication.v1",
        "asset_codec": "marimo.blob-asset.msgpack.v1",
        "notebook": {
            "filename": "finance.py",
            "document_sha256": "0" * 64,
        },
        "producer": {"marimo": "1.0", "marimo_export": "1.0"},
        "variants": {
            "current": {
                "controls": {},
                "outputs": {
                    "summary": {
                        "formats": {
                            format_name: {
                                "format_id": format_id,
                                "media_type": media_type,
                                "metadata": {},
                                "asset": {
                                    "key": key,
                                    "sha256": hashlib.sha256(envelope).hexdigest(),
                                    "size": len(envelope),
                                },
                            }
                        }
                    }
                },
            }
        },
    }
    asset = root / "cache" / key
    asset.parent.mkdir(parents=True)
    asset.write_bytes(envelope)
    (root / "index.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
    return root


def _spec_file(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema": "marimo-export.spec.v1",
                "outputs": {"summary": {"source": "summary", "formats": {"json": {}}}},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_inspect_json_emits_one_result_object(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    publication = _publication(tmp_path / "publication")

    exit_code = cli.main(["inspect", str(publication), "--json"])

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert exit_code == 0
    assert captured.out.count("\n") == 1
    assert captured.err == ""
    assert result["ok"] is True
    assert result["result"]["schema"] == "marimo-export.publication.v1"


def test_read_json_returns_the_decoded_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    publication = _publication(tmp_path / "publication")

    exit_code = cli.main(
        [
            "read",
            str(publication),
            "summary",
            "--variant",
            "current",
            "--format",
            "json",
            "--json",
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert result == {
        "ok": True,
        "result": {
            "format": "json",
            "format_id": "json.v1",
            "media_type": "application/json",
            "output": "summary",
            "value": {"answer": 42},
            "variant": "current",
        },
    }


def test_binary_read_requires_an_output_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    publication = _publication(
        tmp_path / "publication",
        data=b"\x89PNG",
        format_name="png",
        format_id="png.v1",
        media_type="image/png",
    )

    exit_code = cli.main(
        [
            "read",
            str(publication),
            "summary",
            "--variant",
            "current",
            "--format",
            "png",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == cli.EXIT_INPUT
    assert captured.out == ""
    assert "binary output requires --to FILE" in captured.err


@pytest.mark.parametrize(
    ("output_name", "variant_name", "format_name", "expected_details"),
    [
        (
            "summary",
            "missing",
            "json",
            {
                "kind": "variant",
                "name": "missing",
                "name_truncated": False,
                "available": ["current"],
                "available_count": 1,
                "available_truncated": False,
            },
        ),
        (
            "missing",
            "current",
            "json",
            {
                "kind": "output",
                "name": "missing",
                "name_truncated": False,
                "available": ["summary"],
                "available_count": 1,
                "available_truncated": False,
            },
        ),
        (
            "summary",
            "current",
            "missing",
            {
                "kind": "format",
                "name": "missing",
                "name_truncated": False,
                "available": ["json"],
                "available_count": 1,
                "available_truncated": False,
            },
        ),
    ],
)
def test_read_selection_errors_are_input_errors(
    output_name: str,
    variant_name: str,
    format_name: str,
    expected_details: dict[str, object],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    publication = _publication(tmp_path / "publication")

    exit_code = cli.main(
        [
            "read",
            str(publication),
            output_name,
            "--variant",
            variant_name,
            "--format",
            format_name,
            "--json",
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert exit_code == cli.EXIT_INPUT
    assert result["ok"] is False
    assert result["error"]["code"] == "not_found"
    assert result["error"]["details"] == expected_details


def test_binary_read_writes_exact_bytes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publication = _publication(
        tmp_path / "publication",
        data=b"\x89PNG",
        format_name="png",
        format_id="png.v1",
        media_type="image/png",
    )
    output = tmp_path / "chart.png"

    exit_code = cli.main(
        [
            "read",
            str(publication),
            "summary",
            "--variant",
            "current",
            "--format",
            "png",
            "--to",
            str(output),
            "--json",
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output.read_bytes() == b"\x89PNG"
    assert result["result"]["bytes"] == 4
    assert result["result"]["format_id"] == "png.v1"
    assert result["result"]["path"] == str(output.absolute())


@pytest.mark.parametrize("data", [b"hello", b"hello\n"])
def test_text_read_writes_exact_text(
    data: bytes,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    publication = _publication(
        tmp_path / "publication",
        data=data,
        format_name="text",
        format_id="text.v1",
        media_type="text/plain",
    )

    exit_code = cli.main(
        [
            "read",
            str(publication),
            "summary",
            "--variant",
            "current",
            "--format",
            "text",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == data.decode()
    assert captured.err == ""


def test_read_output_preserves_existing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    publication = _publication(tmp_path / "publication")
    output = tmp_path / "result.json"
    output.write_bytes(b"existing")

    exit_code = cli.main(
        [
            "read",
            str(publication),
            "summary",
            "--variant",
            "current",
            "--format",
            "json",
            "--to",
            str(output),
            "--json",
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert exit_code == cli.EXIT_FILESYSTEM
    assert result["error"]["code"] == "destination_exists"
    assert output.read_bytes() == b"existing"
    assert list(tmp_path.glob(".result.json.tmp-*")) == []


def test_read_output_failure_leaves_no_partial_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    publication = _publication(tmp_path / "publication")
    output = tmp_path / "result.json"

    def fail_sync(descriptor: int) -> None:
        raise OSError(f"cannot sync {descriptor}")

    monkeypatch.setattr(cli.os, "fsync", fail_sync)

    exit_code = cli.main(
        [
            "read",
            str(publication),
            "summary",
            "--variant",
            "current",
            "--format",
            "json",
            "--to",
            str(output),
            "--json",
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert exit_code == cli.EXIT_FILESYSTEM
    assert result["error"]["code"] == "filesystem_error"
    assert not output.exists()
    assert list(tmp_path.glob(".result.json.tmp-*")) == []


def test_verify_reports_asset_count(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publication = _publication(tmp_path / "publication")

    exit_code = cli.main(["verify", str(publication), "--json"])

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert result["result"] == {
        "assets": 1,
        "path": str(publication.absolute()),
        "verified": True,
    }


@pytest.mark.parametrize(
    "arguments",
    [
        ["verify", "{publication}"],
        [
            "read",
            "{publication}",
            "summary",
            "--variant",
            "current",
            "--format",
            "json",
        ],
    ],
)
def test_publication_commands_enforce_the_cli_asset_limit(
    arguments: list[str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    publication = _publication(tmp_path / "publication")
    command = [str(publication) if item == "{publication}" else item for item in arguments]

    exit_code = cli.main([*command, "--max-asset-bytes", "1", "--json"])

    result = json.loads(capsys.readouterr().out)
    assert exit_code == cli.EXIT_INTEGRITY
    assert result["error"]["code"] == "publication_error"


@pytest.mark.parametrize(
    "arguments",
    [
        ["inspect", "{publication}"],
        ["verify", "{publication}"],
        [
            "read",
            "{publication}",
            "summary",
            "--variant",
            "current",
            "--format",
            "json",
        ],
    ],
)
def test_publication_commands_enforce_the_cli_index_limit(
    arguments: list[str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    publication = _publication(tmp_path / "publication")
    command = [str(publication) if item == "{publication}" else item for item in arguments]

    exit_code = cli.main([*command, "--max-index-bytes", "1", "--json"])

    result = json.loads(capsys.readouterr().out)
    assert exit_code == cli.EXIT_INTEGRITY
    assert result["error"]["code"] == "publication_error"


def test_read_expands_home_in_publication_and_output_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _publication(home / "publication")
    monkeypatch.setenv("HOME", str(home))

    exit_code = cli.main(
        [
            "read",
            "~/publication",
            "summary",
            "--variant",
            "current",
            "--format",
            "json",
            "--to",
            "~/answer.json",
            "--json",
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert (home / "answer.json").read_bytes() == b'{"answer":42}'
    assert result["result"]["path"] == str(home / "answer.json")


def test_session_help_names_credential_environment(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["session", "--help"])

    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "MARIMO_EXPORT_TOKEN" in output
    assert "MARIMO_EXPORT_SERVER_TOKEN" in output


def test_root_version_reports_the_installed_package_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["--version"])

    assert raised.value.code == 0
    assert capsys.readouterr().out == f"marimo-export {version('marimo-export')}\n"


@pytest.mark.parametrize(
    ("error", "expected_exit"),
    [
        (TransportError("unreachable"), cli.EXIT_TRANSPORT),
        (SessionError("ambiguous session"), cli.EXIT_SESSION),
        (CaptureError("projection failed"), cli.EXIT_CAPTURE),
    ],
)
def test_json_error_exit_classes(
    error: BaseException,
    expected_exit: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FailingClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> FailingClient:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def session(self, session_id: str | None = None) -> object:
            raise error

    monkeypatch.setattr(cli, "Client", FailingClient)

    exit_code = cli.main(
        [
            "session",
            "http://localhost:3456",
            "--session",
            "session-1",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert exit_code == expected_exit
    assert captured.out.count("\n") == 1
    assert captured.err == ""
    assert result["ok"] is False


def test_json_error_preserves_structured_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    error = SessionError("session failed", details={"session_id": "session-1"})

    class FailingClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> FailingClient:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def session(self, session_id: str | None = None) -> object:
            raise error

    monkeypatch.setattr(cli, "Client", FailingClient)

    exit_code = cli.main(
        [
            "session",
            "http://localhost:3456",
            "--session",
            "session-1",
            "--json",
        ]
    )

    assert exit_code == cli.EXIT_SESSION
    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "error": {
            "code": "session_error",
            "message": "session failed",
            "details": {"session_id": "session-1"},
        },
    }


def test_session_uses_cli_credential_environment(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    received: dict[str, object] = {}

    class Description:
        def to_dict(self) -> dict[str, object]:
            return {"session_id": "session-1"}

    class SelectedSession:
        def inspect(self) -> Description:
            return Description()

    class RecordingClient:
        def __init__(self, server: str, **kwargs: object) -> None:
            received["server"] = server
            received.update(kwargs)

        def __enter__(self) -> RecordingClient:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def session(self, session_id: str | None = None) -> SelectedSession:
            received["session_id"] = session_id
            return SelectedSession()

    monkeypatch.setattr(cli, "Client", RecordingClient)
    monkeypatch.setenv("MARIMO_EXPORT_TOKEN", "access-secret")
    monkeypatch.setenv("MARIMO_EXPORT_SERVER_TOKEN", "server-secret")

    exit_code = cli.main(
        [
            "session",
            "http://localhost:3456/",
            "--session",
            "session-1",
            "--json",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert received == {
        "server": "http://localhost:3456/",
        "access_token": "access-secret",
        "server_token": "server-secret",
        "timeout": 300.0,
        "max_index_bytes": 16 * 1024 * 1024,
        "max_asset_bytes": 64 * 1024 * 1024,
        "max_publication_bytes": 512 * 1024 * 1024,
        "session_id": "session-1",
    }


def test_session_without_id_lists_active_sessions(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class ActiveSession:
        def __init__(self, session_id: str, filename: str | None, path: str | None) -> None:
            self.id = session_id
            self.filename = filename
            self.path = path

    class ListingClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> ListingClient:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def sessions(self) -> tuple[ActiveSession, ...]:
            return (
                ActiveSession("session-1", "finance.py", "/srv/finance.py"),
                ActiveSession("session-2", None, None),
            )

    monkeypatch.setattr(cli, "Client", ListingClient)

    exit_code = cli.main(["session", "http://localhost:3456/", "--json"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "result": {
            "sessions": [
                {
                    "id": "session-1",
                    "filename": "finance.py",
                    "path": "/srv/finance.py",
                },
                {"id": "session-2", "filename": None, "path": None},
            ]
        },
    }


def test_capture_passes_the_cli_asset_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    received: dict[str, object] = {}
    spec_path = _spec_file(tmp_path / "finance.export.json")
    destination = tmp_path / "publication"

    class Result:
        def to_dict(self) -> dict[str, object]:
            return {"path": "/tmp/publication"}

    class SelectedSession:
        def capture(self, **kwargs: object) -> Result:
            received["capture"] = kwargs
            return Result()

    class RecordingClient:
        def __init__(self, server: str, **kwargs: object) -> None:
            received["server"] = server
            received.update(kwargs)

        def __enter__(self) -> RecordingClient:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def session(self, session_id: str | None = None) -> SelectedSession:
            received["session_id"] = session_id
            return SelectedSession()

    monkeypatch.setattr(cli, "Client", RecordingClient)

    exit_code = cli.main(
        [
            "capture",
            "http://localhost:3456/",
            "--spec",
            str(spec_path),
            "--output",
            str(destination),
            "--max-asset-bytes",
            "1234",
            "--max-index-bytes",
            "5678",
            "--max-publication-bytes",
            "9012",
            "--json",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert received["max_asset_bytes"] == 1234
    assert received["max_index_bytes"] == 5678
    assert received["max_publication_bytes"] == 9012
    capture_arguments = received["capture"]
    assert isinstance(capture_arguments, dict)
    capture_arguments = cast(dict[str, object], capture_arguments)
    assert isinstance(capture_arguments["spec"], ExportSpec)
    assert capture_arguments["into"] == destination.absolute()
    assert capture_arguments["replace"] is False


@pytest.mark.parametrize("failure", ["spec", "destination"])
def test_capture_validates_local_inputs_before_constructing_client(
    failure: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spec_path = tmp_path / "finance.export.json"
    destination = tmp_path / "publication"
    if failure == "spec":
        spec_path.write_text("{}", encoding="utf-8")
    else:
        _spec_file(spec_path)
        destination.mkdir()

    class UnexpectedClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("Client must not be constructed")

    monkeypatch.setattr(cli, "Client", UnexpectedClient)

    exit_code = cli.main(
        [
            "capture",
            "http://localhost:3456/",
            "--spec",
            str(spec_path),
            "--output",
            str(destination),
            "--json",
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert exit_code in {cli.EXIT_INPUT, cli.EXIT_FILESYSTEM}
    assert result["ok"] is False


def test_unexpected_failures_are_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(path: object, **kwargs: object) -> object:
        del path, kwargs
        raise RuntimeError("internal path and secret")

    monkeypatch.setattr(cli, "open_publication", fail)

    exit_code = cli.main(["inspect", "publication", "--json"])

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert result == {
        "ok": False,
        "error": {
            "code": "internal_error",
            "message": "marimo-export encountered an unexpected internal error",
        },
    }


def test_unexpected_type_errors_are_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(path: object, **kwargs: object) -> object:
        del path, kwargs
        raise TypeError("internal type detail")

    monkeypatch.setattr(cli, "open_publication", fail)

    exit_code = cli.main(["inspect", "publication", "--json"])

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "error": {
            "code": "internal_error",
            "message": "marimo-export encountered an unexpected internal error",
        },
    }


def test_option_prefixes_are_rejected(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    publication = _publication(tmp_path / "publication")

    with pytest.raises(SystemExit) as raised:
        cli.main(["inspect", str(publication), "--j"])

    assert raised.value.code == cli.EXIT_INPUT
    assert "unrecognized arguments: --j" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["session", "capture"])
def test_session_identifier_is_validated_as_cli_input(
    command: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = [command, "http://localhost:3456/"]
    if command == "capture":
        arguments.extend(
            [
                "--spec",
                str(_spec_file(tmp_path / "finance.export.json")),
                "--output",
                str(tmp_path / "publication"),
            ]
        )
    arguments.extend(["--session", "x" * 1025, "--json"])

    with pytest.raises(SystemExit) as raised:
        cli.main(arguments)

    result = json.loads(capsys.readouterr().out)
    assert raised.value.code == cli.EXIT_INPUT
    assert result["error"]["code"] == "invalid_arguments"
    assert result["error"]["message"].endswith("must be a non-empty marimo session ID")


def test_broken_stdout_pipe_returns_conventional_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = _publication(tmp_path / "publication")

    class BrokenStream:
        def __init__(self) -> None:
            self.closed = False

        def write(self, value: str) -> int:
            del value
            raise BrokenPipeError

        def close(self) -> None:
            self.closed = True

    stream = BrokenStream()
    monkeypatch.setattr(cli.sys, "stdout", stream)

    exit_code = cli.main(["inspect", str(publication), "--json"])

    assert exit_code == cli.EXIT_BROKEN_PIPE
    assert stream.closed is True


def test_byte_limits_reject_values_above_the_wire_integer_range(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(
            [
                "inspect",
                "publication",
                "--max-index-bytes",
                str(2**53),
                "--json",
            ]
        )

    result = json.loads(capsys.readouterr().out)
    assert raised.value.code == cli.EXIT_INPUT
    assert result["error"]["code"] == "invalid_arguments"
    assert result["error"]["message"].endswith("must be at most 9007199254740991")


def test_invalid_arguments_have_a_json_error_envelope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["read", "somewhere", "summary", "--json"])

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert raised.value.code == cli.EXIT_INPUT
    assert captured.out.count("\n") == 1
    assert captured.err == ""
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_arguments"


@pytest.mark.parametrize("json_mode", [False, True])
def test_invalid_argument_messages_redact_credentials(
    json_mode: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MARIMO_EXPORT_TOKEN", "ENV_ACCESS_SECRET")
    monkeypatch.setenv("MARIMO_EXPORT_SERVER_TOKEN", "ENV_SERVER_SECRET")
    arguments = [
        "session",
        "http://localhost:3456/",
        "--bogus",
        "http://localhost:3456/?access_token=URL_SECRET",
        "ENV_ACCESS_SECRET",
        "ENV_SERVER_SECRET",
    ]
    if json_mode:
        arguments.append("--json")

    with pytest.raises(SystemExit) as raised:
        cli.main(arguments)

    captured = capsys.readouterr()
    output = captured.out if json_mode else captured.err
    assert raised.value.code == cli.EXIT_INPUT
    assert "URL_SECRET" not in output
    assert "ENV_ACCESS_SECRET" not in output
    assert "ENV_SERVER_SECRET" not in output
    assert "access_token=<redacted>" in output


@pytest.mark.parametrize("json_mode", [False, True])
def test_runtime_errors_and_details_redact_credentials(
    json_mode: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MARIMO_EXPORT_TOKEN", "ENV_ACCESS_SECRET")
    monkeypatch.setenv("MARIMO_EXPORT_SERVER_TOKEN", "ENV_SERVER_SECRET")

    class FailingClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> FailingClient:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def session(self, session_id: str) -> object:
            del session_id
            raise SessionError(
                "failed for http://user:password@example.test/"
                "?access_token=URL_SECRET ENV_ACCESS_SECRET",
                details={"ENV_SERVER_SECRET": ["http://example.test/?server_token=DETAIL_SECRET"]},
            )

    monkeypatch.setattr(cli, "Client", FailingClient)
    arguments = ["session", "http://localhost:3456/", "--session", "missing"]
    if json_mode:
        arguments.append("--json")

    exit_code = cli.main(arguments)

    captured = capsys.readouterr()
    output = captured.out if json_mode else captured.err
    assert exit_code == cli.EXIT_SESSION
    assert "password" not in output
    assert "URL_SECRET" not in output
    assert "DETAIL_SECRET" not in output
    assert "ENV_ACCESS_SECRET" not in output
    assert "ENV_SERVER_SECRET" not in output
    assert "<redacted>" in output
