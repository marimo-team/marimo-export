from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import marimo_export.client as client_module
import msgspec
import pytest
from marimo_export._remote import BridgeError, SessionInfo
from marimo_export.client import (
    BuiltinExporterDescription,
    CacheSummary,
    CellDescription,
    Client,
    ControlDescription,
    GlobalDescription,
    Session,
    capture,
)
from marimo_export.errors import (
    CaptureError,
    IntegrityError,
    PublicationError,
    SelectionError,
    SessionError,
    TransferError,
    TransportError,
)
from marimo_export.reader import open_publication
from marimo_export.spec import ExportSpec


def _spec() -> ExportSpec:
    return ExportSpec.from_value(
        {
            "schema": "marimo-export.spec.v1",
            "outputs": {"summary": {"source": "summary", "formats": {"json": {}}}},
        }
    )


def _blob_asset(
    *,
    data: bytes = b'{"answer":42}',
    media_type: str = "application/json",
    format_id: str = "json.v1",
) -> bytes:
    return msgspec.msgpack.encode(
        {
            "data": data,
            "media_type": media_type,
            "filename": None,
            "metadata": {"format_id": format_id, "metadata_json": b"{}"},
        }
    )


def _index(payload: bytes, *, key: str = "project/abc/return.bin") -> dict[str, Any]:
    return {
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
                            "json": {
                                "format_id": "json.v1",
                                "media_type": "application/json",
                                "metadata": {},
                                "asset": {
                                    "key": key,
                                    "sha256": hashlib.sha256(payload).hexdigest(),
                                    "size": len(payload),
                                },
                            }
                        }
                    }
                },
            }
        },
    }


class FakeTransport:
    def __init__(
        self,
        payload: bytes | None = None,
        *,
        key: str = "project/abc/return.bin",
    ) -> None:
        self.info = SessionInfo("session-1", "finance.py", "/notebooks/finance.py")
        self.sessions_info: tuple[SessionInfo, ...] = (self.info,)
        self.payload = _blob_asset() if payload is None else payload
        self.invocations: list[tuple[str, str, Mapping[str, object]]] = []
        self.downloads: list[tuple[str, str, int]] = []
        self.capture_error: BaseException | None = None
        self.release_error: BaseException | None = None
        self.inspect_response: dict[str, object] = {
            "marimo_version": "0.23.14",
            "marimo_export_version": "0.0.0",
            "notebook": {
                "filename": "finance.py",
                "path": "/notebooks/finance.py",
                "document_sha256": "0" * 64,
            },
            "globals": [{"name": "summary", "python_type": "builtins.dict"}],
            "cells": [
                {
                    "id": "cell-1",
                    "name": "summary_cell",
                    "status": "idle",
                    "has_output": True,
                    "media_type": "application/json",
                }
            ],
            "controls": [
                {
                    "name": "symbol_picker",
                    "type": "Dropdown",
                    "value": ["AAPL"],
                    "sensitive": False,
                    "domain": {"options": ["AAPL", "MSFT"]},
                }
            ],
            "builtin_exporters": [
                {
                    "name": "json",
                    "format_id": "json.v1",
                    "available": True,
                    "extra": None,
                }
            ],
        }
        index = _index(self.payload, key=key)
        asset = index["variants"]["current"]["outputs"]["summary"]["formats"]["json"]["asset"]
        self.capture_response: dict[str, Any] = {
            "ticket": "ticket-1",
            "expires_at_ms": 2_000_000_000_000,
            "index": index,
            "assets": [{**asset, "url": "./@file/cache-asset"}],
            "cache": {"hits": 1, "misses": 0, "skipped": 0},
        }

    def list_sessions(self) -> tuple[SessionInfo, ...]:
        return self.sessions_info

    def invoke(
        self, session_id: str, operation: str, params: Mapping[str, object]
    ) -> dict[str, object]:
        self.invocations.append((session_id, operation, params))
        if operation == "inspect":
            return self.inspect_response
        if operation == "capture":
            if self.capture_error is not None:
                raise self.capture_error
            return self.capture_response
        if operation == "release":
            if self.release_error is not None:
                raise self.release_error
            return {"released": True}
        raise AssertionError(operation)

    def download_asset(self, session_id: str, url: str, maximum_bytes: int) -> bytes:
        self.downloads.append((session_id, url, maximum_bytes))
        return self.payload


def test_session_inspects_live_notebook_state() -> None:
    transport = FakeTransport()
    client = Client._from_transport(transport)

    description = client.session().inspect()

    assert description.to_dict() == {
        "session_id": "session-1",
        "filename": "finance.py",
        "path": "/notebooks/finance.py",
        "document_sha256": "0" * 64,
        "marimo_version": "0.23.14",
        "marimo_export_version": "0.0.0",
        "globals": [{"name": "summary", "python_type": "builtins.dict"}],
        "cells": [
            {
                "id": "cell-1",
                "name": "summary_cell",
                "status": "idle",
                "has_output": True,
                "media_type": "application/json",
            }
        ],
        "controls": [
            {
                "name": "symbol_picker",
                "type": "Dropdown",
                "value": ["AAPL"],
                "sensitive": False,
                "domain": {"options": ["AAPL", "MSFT"]},
            }
        ],
        "builtin_exporters": [
            {
                "name": "json",
                "format_id": "json.v1",
                "available": True,
                "extra": None,
            }
        ],
    }
    assert description.cells == (
        CellDescription(
            id="cell-1",
            name="summary_cell",
            status="idle",
            has_output=True,
            media_type="application/json",
        ),
    )
    assert description.globals == (GlobalDescription(name="summary", python_type="builtins.dict"),)
    assert description.controls == (
        ControlDescription(
            "symbol_picker",
            "Dropdown",
            ["AAPL"],
            sensitive=False,
            domain={"options": ["AAPL", "MSFT"]},
        ),
    )
    assert description.builtin_exporters == (
        BuiltinExporterDescription(
            name="json",
            format_id="json.v1",
            available=True,
            extra=None,
        ),
    )
    control_value = description.controls[0].value
    assert isinstance(control_value, list)
    control_value.append("MSFT")
    assert description.controls[0].value == ["AAPL"]
    control_domain = description.controls[0].domain
    control_domain["options"] = []
    assert description.controls[0].domain == {"options": ["AAPL", "MSFT"]}
    assert [operation for _, operation, _ in transport.invocations] == ["inspect"]


def test_session_redacts_sensitive_control_values() -> None:
    transport = FakeTransport()
    transport.inspect_response["controls"] = [
        {
            "name": "password",
            "type": "Text",
            "value": None,
            "sensitive": True,
            "domain": {},
        }
    ]

    control = Client._from_transport(transport).session().inspect().controls[0]

    assert control.sensitive is True
    assert control.value is None
    assert control.to_dict() == {
        "name": "password",
        "type": "Text",
        "value": None,
        "sensitive": True,
        "domain": {},
    }


def test_session_rejects_an_unredacted_sensitive_control() -> None:
    transport = FakeTransport()
    transport.inspect_response["controls"] = [
        {
            "name": "password",
            "type": "Text",
            "value": "secret",
            "sensitive": True,
            "domain": {},
        }
    ]

    with pytest.raises(SessionError, match="invalid inspect response"):
        Client._from_transport(transport).session().inspect()


def test_session_rejects_an_unredacted_sensitive_control_domain() -> None:
    transport = FakeTransport()
    transport.inspect_response["controls"] = [
        {
            "name": "password",
            "type": "Text",
            "value": None,
            "sensitive": True,
            "domain": {"options": ["secret"]},
        }
    ]

    with pytest.raises(SessionError, match="invalid inspect response"):
        Client._from_transport(transport).session().inspect()


def test_session_rejects_malformed_inspection_as_a_session_error() -> None:
    transport = FakeTransport()
    transport.inspect_response = {"notebook": {"document_sha256": "invalid"}}

    with pytest.raises(SessionError, match="inspect response"):
        Client._from_transport(transport).session().inspect()


def test_session_rejects_unsorted_builtin_exporter_discovery() -> None:
    transport = FakeTransport()
    transport.inspect_response["builtin_exporters"] = [
        {"name": "text", "format_id": "text.v1", "available": True, "extra": None},
        {"name": "json", "format_id": "json.v1", "available": True, "extra": None},
    ]

    with pytest.raises(SessionError, match="sorted by name"):
        Client._from_transport(transport).session().inspect()


def test_session_rejects_unsorted_global_discovery() -> None:
    transport = FakeTransport()
    transport.inspect_response["globals"] = [
        {"name": "z_value", "python_type": "builtins.int"},
        {"name": "a_value", "python_type": "builtins.str"},
    ]

    with pytest.raises(SessionError, match="sorted by name"):
        Client._from_transport(transport).session().inspect()


def test_global_description_bounds_discovery_strings() -> None:
    with pytest.raises(TypeError, match="at most 512 UTF-8 bytes"):
        GlobalDescription(name="value", python_type="\N{EURO SIGN}" * 171)


def test_client_lists_sessions_for_explicit_selection() -> None:
    transport = FakeTransport()
    second = SessionInfo("session-2", "report.py", "/notebooks/report.py")
    transport.sessions_info = (transport.info, second)
    client = Client._from_transport(transport)

    assert [session.id for session in client.sessions()] == ["session-1", "session-2"]
    assert client.session("session-2").filename == "report.py"
    with pytest.raises(SessionError) as raised:
        client.session()
    assert raised.value.details == {
        "sessions": [
            {
                "id": "session-1",
                "filename": "finance.py",
                "path": "/notebooks/finance.py",
            },
            {
                "id": "session-2",
                "filename": "report.py",
                "path": "/notebooks/report.py",
            },
        ],
        "session_count": 2,
        "sessions_truncated": False,
    }


def test_session_selection_errors_bound_and_redact_discovery_details() -> None:
    transport = FakeTransport()
    transport.sessions_info = (
        *(
            SessionInfo(
                f"session-{index:02d}",
                f"report-{index:02d}.py",
                f"/notebooks/report-{index:02d}.py",
            )
            for index in reversed(range(24))
        ),
        SessionInfo(
            "https://user:password@example.test/?access_token=url-secret",
            "configured-secret.py",
            "/notebooks/configured-secret.py",
        ),
    )
    client = Client._from_transport(transport)
    client._diagnostic_secrets = ("configured-secret",)

    with pytest.raises(SessionError) as raised:
        client.session(
            "https://user:password@example.test/?access_token=url-secret&configured-secret"
        )

    rendered = json.dumps(raised.value.wire())
    assert "password" not in rendered
    assert "url-secret" not in rendered
    assert "configured-secret" not in rendered
    assert raised.value.details["session_count"] == 25
    assert raised.value.details["sessions_truncated"] is True
    sessions = cast(list[dict[str, object]], raised.value.details["sessions"])
    assert len(sessions) == 16
    identifiers = [item["id"] for item in sessions]
    assert identifiers == [
        "https://<redacted>@example.test/?access_token=<redacted>",
        *(f"session-{index:02d}" for index in range(15)),
    ]


def test_session_selection_reports_empty_discovery() -> None:
    transport = FakeTransport()
    transport.sessions_info = ()

    with pytest.raises(SessionError) as raised:
        Client._from_transport(transport).session()

    assert raised.value.details == {
        "sessions": [],
        "session_count": 0,
        "sessions_truncated": False,
    }


def test_session_selection_rejects_control_characters() -> None:
    with pytest.raises(TypeError, match="marimo session ID"):
        Client._from_transport(FakeTransport()).session("session\x1b[31m")


def test_session_selection_redacts_access_token_from_client_url() -> None:
    client = Client("http://localhost:3456/?access_token=url-secret")
    client._transport = FakeTransport()

    with pytest.raises(SessionError) as raised:
        client.session("url-secret")

    assert "url-secret" not in str(raised.value)


def test_client_returns_a_read_only_session_protocol() -> None:
    session = Client._from_transport(FakeTransport()).session()

    assert isinstance(session, Session)
    with pytest.raises(AttributeError):
        object.__setattr__(session, "id", "other")


def test_capture_verifies_assets_and_commits_index_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = FakeTransport()
    writes: list[str] = []
    original_write = client_module._write_file

    def record_write(path: Path, payload: bytes) -> None:
        writes.append(path.name)
        original_write(path, payload)

    monkeypatch.setattr(client_module, "_write_file", record_write)
    result = (
        Client._from_transport(transport)
        .session()
        .capture(spec=_spec(), into=tmp_path / "publication")
    )

    assert result.to_dict() == {
        "path": str((tmp_path / "publication").absolute()),
        "session_id": "session-1",
        "variants": ["current"],
        "outputs": ["summary"],
        "assets": 1,
        "bytes_transferred": len(transport.payload),
        "cache": {"hits": 1, "misses": 0, "skipped": 0},
    }
    assert result.cache == CacheSummary(hits=1, misses=0, skipped=0)
    assert (tmp_path / "publication/cache/project/abc/return.bin").read_bytes() == (
        transport.payload
    )
    assert (tmp_path / "publication/index.json").is_file()
    assert writes[-1] == "index.json"
    assert transport.downloads == [("session-1", "./@file/cache-asset", len(transport.payload))]
    assert [operation for _, operation, _ in transport.invocations] == [
        "capture",
        "inspect",
        "release",
    ]


def test_top_level_capture_composes_client_and_session_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = FakeTransport()
    received: dict[str, object] = {}

    def transport_factory(server: str, **kwargs: object) -> FakeTransport:
        received["server"] = server
        received.update(kwargs)
        return transport

    monkeypatch.setattr(client_module, "HttpKernelTransport", transport_factory)

    result = capture(
        "http://localhost:3456/",
        spec=_spec(),
        into=tmp_path / "publication",
        session="session-1",
        access_token="access-secret",
        server_token="server-secret",
        timeout=12.0,
    )

    assert result.session_id == "session-1"
    assert received == {
        "server": "http://localhost:3456/",
        "access_token": "access-secret",
        "server_token": "server-secret",
        "timeout": 12.0,
        "maximum_event_bytes": 40 * 1024 * 1024,
        "maximum_response_bytes": 40 * 1024 * 1024,
    }


def test_top_level_capture_validates_replace_before_client_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnexpectedClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("Client must not be constructed")

    monkeypatch.setattr(client_module, "Client", UnexpectedClient)

    with pytest.raises(TypeError, match="replace must be a boolean"):
        capture(
            "http://localhost:3456/",
            spec=_spec(),
            into=tmp_path / "publication",
            replace=cast(Any, "yes"),
        )


def test_capture_preserves_integrity_failure_when_release_also_fails(
    tmp_path: Path,
) -> None:
    transport = FakeTransport()
    transport.payload = b"tampered"
    transport.release_error = TransportError("release failed")

    with pytest.raises(IntegrityError, match="size"):
        Client._from_transport(transport).session().capture(
            spec=_spec(), into=tmp_path / "publication"
        )

    assert [operation for _, operation, _ in transport.invocations] == [
        "capture",
        "inspect",
        "release",
    ]
    assert not (tmp_path / "publication").exists()


def test_capture_rejects_same_size_asset_corruption(tmp_path: Path) -> None:
    transport = FakeTransport()
    transport.payload = b"x" * len(transport.payload)

    with pytest.raises(IntegrityError, match="SHA-256"):
        Client._from_transport(transport).session().capture(
            spec=_spec(), into=tmp_path / "publication"
        )

    assert not (tmp_path / "publication").exists()
    assert [operation for _, operation, _ in transport.invocations] == [
        "capture",
        "inspect",
        "release",
    ]


def test_capture_rejects_malformed_blob_asset_before_commit(tmp_path: Path) -> None:
    transport = FakeTransport(payload=b"not a BlobAsset")

    with pytest.raises(IntegrityError, match="BlobAsset envelope"):
        Client._from_transport(transport).session().capture(
            spec=_spec(), into=tmp_path / "publication"
        )

    assert not (tmp_path / "publication").exists()


def test_capture_rejects_document_change_before_asset_download(tmp_path: Path) -> None:
    transport = FakeTransport()
    notebook = transport.inspect_response["notebook"]
    assert isinstance(notebook, dict)
    notebook = cast(dict[str, object], notebook)
    notebook["document_sha256"] = "1" * 64

    with pytest.raises(CaptureError, match="document changed") as raised:
        Client._from_transport(transport).session().capture(
            spec=_spec(), into=tmp_path / "publication"
        )

    assert raised.value.details == {"captured": "0" * 64, "current": "1" * 64}
    assert transport.downloads == []
    assert [operation for _, operation, _ in transport.invocations] == [
        "capture",
        "inspect",
        "release",
    ]


def test_capture_rejects_blob_asset_contract_disagreement_before_commit(
    tmp_path: Path,
) -> None:
    transport = FakeTransport(payload=_blob_asset(media_type="text/plain"))

    with pytest.raises(IntegrityError, match="media type disagrees"):
        Client._from_transport(transport).session().capture(
            spec=_spec(), into=tmp_path / "publication"
        )

    assert not (tmp_path / "publication").exists()


def test_capture_rejects_asset_over_client_limit_before_download(tmp_path: Path) -> None:
    transport = FakeTransport()
    limit = len(transport.payload) - 1

    with pytest.raises(TransferError, match="capture limit") as raised:
        Client._from_transport(
            transport,
            max_asset_bytes=limit,
        ).session().capture(spec=_spec(), into=tmp_path / "publication")

    assert raised.value.details == {
        "asset": "project/abc/return.bin",
        "size": len(transport.payload),
        "limit": limit,
    }
    assert transport.downloads == []
    assert not (tmp_path / "publication").exists()


def test_client_rejects_invalid_asset_limit() -> None:
    with pytest.raises(ValueError, match="max_asset_bytes"):
        Client._from_transport(FakeTransport(), max_asset_bytes=0)


def test_client_rejects_invalid_index_limit() -> None:
    with pytest.raises(ValueError, match="max_index_bytes"):
        Client._from_transport(FakeTransport(), max_index_bytes=0)


def test_client_rejects_invalid_publication_limit() -> None:
    with pytest.raises(ValueError, match="max_publication_bytes"):
        Client._from_transport(FakeTransport(), max_publication_bytes=0)


def test_capture_rejects_index_over_client_limit_before_download(tmp_path: Path) -> None:
    transport = FakeTransport()

    with pytest.raises(CaptureError, match="index exceeds") as raised:
        Client._from_transport(
            transport,
            max_index_bytes=1,
        ).session().capture(spec=_spec(), into=tmp_path / "publication")

    assert raised.value.details == {
        "size": len(client_module.PublicationIndex.from_wire(_index(transport.payload)).to_bytes()),
        "limit": 1,
    }
    assert transport.downloads == []
    capture_params = transport.invocations[0][2]
    assert capture_params["maximum_index_bytes"] == 1


def test_capture_rejects_publication_over_client_limit_before_download(tmp_path: Path) -> None:
    transport = FakeTransport()
    index_size = len(client_module.PublicationIndex.from_wire(_index(transport.payload)).to_bytes())
    limit = index_size + len(transport.payload) - 1

    with pytest.raises(TransferError, match="max_publication_bytes") as raised:
        Client._from_transport(
            transport,
            max_publication_bytes=limit,
        ).session().capture(spec=_spec(), into=tmp_path / "publication")

    assert raised.value.details == {
        "limit": limit,
        "accounted_bytes": index_size,
        "asset": "project/abc/return.bin",
        "asset_size": len(transport.payload),
    }
    assert transport.downloads == []
    capture_params = transport.invocations[0][2]
    assert capture_params["maximum_publication_bytes"] == limit


def test_capture_rejects_excessive_asset_count_before_download(tmp_path: Path) -> None:
    transport = FakeTransport()
    transport.capture_response["assets"] = [{}] * (client_module._MAX_TRANSFER_ASSETS + 1)

    with pytest.raises(TransferError, match="at most 4096"):
        Client._from_transport(transport).session().capture(
            spec=_spec(),
            into=tmp_path / "publication",
        )

    assert transport.downloads == []


@pytest.mark.parametrize(
    "cache",
    [
        {},
        {"hits": 0, "misses": 0, "skipped": 0, "other": 0},
        {"hits": True, "misses": 0, "skipped": 0},
        {"hits": -1, "misses": 0, "skipped": 0},
        {"hits": 2**53, "misses": 0, "skipped": 0},
    ],
)
def test_capture_rejects_invalid_cache_summary(
    cache: object,
    tmp_path: Path,
) -> None:
    transport = FakeTransport()
    transport.capture_response["cache"] = cache

    with pytest.raises(CaptureError, match="cache"):
        Client._from_transport(transport).session().capture(
            spec=_spec(), into=tmp_path / "publication"
        )

    assert transport.downloads == []


def test_capture_rejects_existing_destination_before_remote_execution(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "publication"
    destination.mkdir()
    transport = FakeTransport()

    with pytest.raises(FileExistsError, match="exists"):
        Client._from_transport(transport).session().capture(spec=_spec(), into=destination)

    assert transport.invocations == []


def test_capture_replace_rejects_an_unrelated_directory_before_remote_execution(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "documents"
    destination.mkdir()
    (destination / "index.json").write_text("unrelated", encoding="utf-8")
    transport = FakeTransport()

    with pytest.raises(PublicationError, match="replacement target"):
        Client._from_transport(transport).session().capture(
            spec=_spec(),
            into=destination,
            replace=True,
        )

    assert transport.invocations == []
    assert (destination / "index.json").read_text(encoding="utf-8") == "unrelated"


def test_capture_replace_revalidates_a_concurrently_created_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "publication"
    transport = FakeTransport()
    original_commit = client_module._commit_directory

    def reserve_then_commit(
        staging: Path,
        target: Path,
        *,
        replace: bool,
        max_index_bytes: int,
        max_asset_bytes: int,
        max_publication_bytes: int,
    ) -> None:
        target.mkdir()
        (target / "index.json").write_text("reserved", encoding="utf-8")
        original_commit(
            staging,
            target,
            replace=replace,
            max_index_bytes=max_index_bytes,
            max_asset_bytes=max_asset_bytes,
            max_publication_bytes=max_publication_bytes,
        )

    monkeypatch.setattr(client_module, "_commit_directory", reserve_then_commit)

    with pytest.raises(PublicationError, match="replacement target"):
        Client._from_transport(transport).session().capture(
            spec=_spec(),
            into=destination,
            replace=True,
        )

    assert (destination / "index.json").read_text(encoding="utf-8") == "reserved"


def test_capture_destination_expands_the_user_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    result = (
        Client._from_transport(FakeTransport())
        .session()
        .capture(spec=_spec(), into="~/publication")
    )

    assert result.path == home / "publication"
    assert (home / "publication" / "index.json").is_file()


def test_capture_preserves_structured_bridge_error_details(tmp_path: Path) -> None:
    transport = FakeTransport()
    transport.capture_error = BridgeError(
        "selection_error",
        "source is unavailable",
        details={"source": "missing_name"},
    )

    with pytest.raises(SelectionError) as raised:
        Client._from_transport(transport).session().capture(
            spec=_spec(), into=tmp_path / "publication"
        )

    assert raised.value.details == {"source": "missing_name"}


def test_capture_syncs_nested_cache_directories_before_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    synced: list[Path] = []
    monkeypatch.setattr(client_module, "_sync_directory", synced.append)

    Client._from_transport(FakeTransport()).session().capture(
        spec=_spec(),
        into=tmp_path / "publication",
    )

    assert any(path.name == "project" and path.parent.name == "cache" for path in synced)
    assert any(path.name == "abc" and path.parent.name == "project" for path in synced)


def test_capture_replaces_existing_publication_after_transfer(tmp_path: Path) -> None:
    destination = tmp_path / "publication"
    Client._from_transport(FakeTransport()).session().capture(spec=_spec(), into=destination)
    old_asset = destination / "cache/project/abc/return.bin"
    replacement = FakeTransport(
        _blob_asset(data=b'{"answer":43}'),
        key="project/def/return.bin",
    )

    Client._from_transport(replacement).session().capture(
        spec=_spec(), into=destination, replace=True
    )

    value = open_publication(destination).variant("current").output("summary").format("json").json()
    assert value == {"answer": 43}
    assert old_asset.is_file()
    assert (destination / "cache/project/def/return.bin").is_file()


def test_replacement_syncs_each_new_cache_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "publication"
    Client._from_transport(FakeTransport()).session().capture(spec=_spec(), into=destination)
    replacement = FakeTransport(
        _blob_asset(data=b'{"answer":43}'),
        key="new/deep/return.bin",
    )
    synced: list[Path] = []
    monkeypatch.setattr(client_module, "_sync_directory", synced.append)

    Client._from_transport(replacement).session().capture(
        spec=_spec(),
        into=destination,
        replace=True,
    )

    assert destination / "cache" / "new" in synced
    assert destination / "cache" / "new" / "deep" in synced


def test_replace_keeps_previous_index_when_index_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "publication"
    Client._from_transport(FakeTransport()).session().capture(spec=_spec(), into=destination)
    replacement = FakeTransport(
        _blob_asset(data=b'{"answer":43}'),
        key="project/def/return.bin",
    )
    original_replace = client_module.os.replace

    def fail_new_commit(source: Path, target: Path) -> None:
        if Path(target) == destination / "index.json":
            raise OSError("commit failed")
        original_replace(source, target)

    monkeypatch.setattr(client_module.os, "replace", fail_new_commit)

    with pytest.raises(OSError, match="commit failed"):
        Client._from_transport(replacement).session().capture(
            spec=_spec(), into=destination, replace=True
        )

    value = open_publication(destination).variant("current").output("summary").format("json").json()
    assert value == {"answer": 42}
    assert list(destination.glob(".index.json.tmp-*")) == []


def test_replace_commits_index_atomically_after_assets_are_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "publication"
    Client._from_transport(FakeTransport()).session().capture(spec=_spec(), into=destination)
    replacement = FakeTransport(
        _blob_asset(data=b'{"answer":43}'),
        key="project/def/return.bin",
    )
    original_replace = client_module.os.replace
    values_before_commit: list[object] = []

    def observe_index_commit(source: Path, target: Path) -> None:
        if Path(target) == destination / "index.json":
            assert destination.is_dir()
            assert (destination / "cache/project/def/return.bin").is_file()
            value = (
                open_publication(destination)
                .variant("current")
                .output("summary")
                .format("json")
                .json()
            )
            values_before_commit.append(value)
        original_replace(source, target)

    monkeypatch.setattr(client_module.os, "replace", observe_index_commit)

    Client._from_transport(replacement).session().capture(
        spec=_spec(), into=destination, replace=True
    )

    assert values_before_commit == [{"answer": 42}]


def test_replace_rejects_same_cache_key_with_different_bytes(tmp_path: Path) -> None:
    destination = tmp_path / "publication"
    Client._from_transport(FakeTransport()).session().capture(spec=_spec(), into=destination)
    replacement = FakeTransport(_blob_asset(data=b'{"answer":43}'))

    with pytest.raises(TransferError, match="already contains different bytes"):
        Client._from_transport(replacement).session().capture(
            spec=_spec(), into=destination, replace=True
        )

    value = open_publication(destination).variant("current").output("summary").format("json").json()
    assert value == {"answer": 42}


def test_commit_succeeds_when_post_commit_directory_sync_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "publication"
    staging = tmp_path / ".publication.tmp-test"
    (staging / "cache").mkdir(parents=True)
    (staging / "cache" / "new.txt").write_text("new", encoding="utf-8")
    (staging / "index.json").write_text("{}", encoding="utf-8")

    def fail_sync(path: Path) -> None:
        raise OSError(f"cannot sync {path}")

    monkeypatch.setattr(client_module, "_sync_directory", fail_sync)

    client_module._commit_directory(staging, destination, replace=False)

    assert (destination / "cache" / "new.txt").read_text(encoding="utf-8") == "new"


def test_new_commit_does_not_replace_concurrently_created_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "publication"
    staging = tmp_path / ".publication.tmp-test"
    (staging / "cache").mkdir(parents=True)
    (staging / "index.json").write_text("{}", encoding="utf-8")
    original_rename = client_module._rename_directory_noreplace

    def reserve_destination(source: Path, target: Path) -> None:
        target.mkdir()
        (target / "reservation.txt").write_text("reserved", encoding="utf-8")
        original_rename(source, target)

    monkeypatch.setattr(client_module, "_rename_directory_noreplace", reserve_destination)

    with pytest.raises(FileExistsError):
        client_module._commit_directory(staging, destination, replace=False)

    assert (destination / "reservation.txt").read_text(encoding="utf-8") == "reserved"
    assert (staging / "index.json").is_file()


def test_capture_reports_committed_publication_when_release_fails(tmp_path: Path) -> None:
    destination = tmp_path / "publication"
    transport = FakeTransport()
    transport.release_error = TransportError("release failed")

    with pytest.raises(TransferError, match="cleanup failed") as raised:
        Client._from_transport(transport).session().capture(spec=_spec(), into=destination)

    assert raised.value.details == {
        "committed": True,
        "path": str(destination.absolute()),
    }
    assert (destination / "index.json").is_file()


def test_capture_rejects_asset_receipt_that_differs_from_index(tmp_path: Path) -> None:
    transport = FakeTransport()
    transport.capture_response["assets"][0]["sha256"] = "f" * 64

    with pytest.raises(TransferError, match="does not match the index"):
        Client._from_transport(transport).session().capture(
            spec=_spec(), into=tmp_path / "publication"
        )

    assert not (tmp_path / "publication").exists()


@pytest.mark.parametrize(
    "key",
    [
        "project//return.bin",
        "project\\return.bin",
        "project/report:stream/return.bin",
        "project/draft./return.bin",
        "project/draft /return.bin",
        "project/CON/return.bin",
        "project/com1.log/return.bin",
        "project/control\x1f/return.bin",
        "project/delete\x7f/return.bin",
        "project/return.json",
        f"project/{'x' * 256}/return.bin",
        "N{EURO SIGN}" * 341 + ".bin",
        f"{'x' * 1021}.bin",
    ],
)
def test_client_rejects_nonportable_cache_asset_keys(key: str) -> None:
    with pytest.raises(TransferError, match="cache asset key"):
        client_module._validate_cache_key(key)


def test_capture_rejects_malformed_receipt_as_a_capture_error(tmp_path: Path) -> None:
    transport = FakeTransport()
    transport.capture_response["unexpected"] = True

    with pytest.raises(CaptureError, match="invalid capture response"):
        Client._from_transport(transport).session().capture(
            spec=_spec(), into=tmp_path / "publication"
        )

    assert [operation for _, operation, _ in transport.invocations] == [
        "capture",
        "inspect",
        "release",
    ]


def test_client_close_does_not_modify_the_remote_session() -> None:
    transport = FakeTransport()

    with Client._from_transport(transport) as client:
        session = client.session()
        assert session.id == "session-1"

    assert transport.invocations == []
    with pytest.raises(RuntimeError, match="closed"):
        client.session()
    with pytest.raises(RuntimeError, match="closed"):
        session.inspect()


def test_closed_session_capture_does_not_create_destination_parent(tmp_path: Path) -> None:
    client = Client._from_transport(FakeTransport())
    session = client.session()
    client.close()
    destination = tmp_path / "missing" / "publication"

    with pytest.raises(RuntimeError, match="closed"):
        session.capture(spec=_spec(), into=destination)

    assert not destination.parent.exists()
