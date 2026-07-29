from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import marimo_export.client as client_module
import pytest
from marimo_export import (
    Client,
    ExportSpec,
    OutputSpec,
    open_publication,
)
from marimo_export._json import JsonObject, sha256_bytes
from marimo_export._remote import BridgeError, SessionInfo
from marimo_export.errors import ExecutionError, PublicationError, SessionError, TransportError
from marimo_export.publication import (
    AssetRef,
    NotebookProvenance,
    NumpyDescriptor,
    ProducerProvenance,
    Provenance,
    PublicationIndex,
    ScalarDescriptor,
    StateEntry,
)


def _spec() -> ExportSpec:
    return ExportSpec(
        inputs=(),
        states={"baseline": {}},
        outputs={"answer": OutputSpec(source="answer")},
    )


def _index(*, asset: bytes | None = None) -> PublicationIndex:
    if asset is None:
        descriptor = ScalarDescriptor(
            value=42,
            provenance=Provenance(
                cache_key="cell_cache/answer.json",
                return_reference=None,
                python_type="builtins.int",
            ),
        )
    else:
        digest = sha256_bytes(asset)
        descriptor = NumpyDescriptor(
            asset=AssetRef(digest, len(asset)),
            provenance=Provenance(
                cache_key="cell_cache/answer.json",
                return_reference="cell_cache/answer/return.npy",
                python_type="numpy.ndarray",
            ),
        )
    return PublicationIndex(
        notebook=NotebookProvenance(
            filename="notebook.py",
            document_sha256="a" * 64,
        ),
        producer=ProducerProvenance(
            marimo="0.23.15",
            marimo_export="1.0.0",
        ),
        inputs=(),
        outputs=("answer",),
        states={
            "baseline": StateEntry(
                inputs={},
                outputs={"answer": descriptor},
            )
        },
    )


def _npy() -> bytes:
    header = repr({"descr": "|u1", "fortran_order": False, "shape": (3,)})
    prefix = b"\x93NUMPY\x01\x00"
    padding = (64 - ((len(prefix) + 2 + len(header) + 1) % 64)) % 64
    header_bytes = (header + " " * padding + "\n").encode("latin1")
    return prefix + len(header_bytes).to_bytes(2, "little") + header_bytes + b"\x01\x02\x03"


def _inspection() -> JsonObject:
    return {
        "filename": "notebook.py",
        "path": "/workspace/notebook.py",
        "document_sha256": "a" * 64,
        "marimo_version": "0.23.15",
        "marimo_export_version": "1.0.0",
        "capabilities": [
            "asset_transfer",
            "blob_asset",
            "cache_cells",
            "cell_cache_receipts",
            "child_sessions",
            "child_ui_updates",
            "definition_overrides",
            "setup_definition_overrides",
            "synthetic_projection_cells",
        ],
        "definitions": [
            {
                "name": "answer",
                "cell_id": "cell-answer",
                "python_type": "builtins.int",
                "kind": "ordinary",
                "siblings": ["answer"],
                "portable_input": True,
                "sensitive": False,
                "value_available": False,
                "value": None,
                "domain": {},
            }
        ],
    }


class _Transport:
    def __init__(
        self,
        index: PublicationIndex | None = None,
        *,
        payload: bytes | None = None,
        release_error: BridgeError | None = None,
    ) -> None:
        self.index = index or _index()
        self.payload = payload
        self.release_error = release_error
        self.calls: list[tuple[str, str]] = []

    def list_sessions(self) -> tuple[SessionInfo, ...]:
        self.calls.append(("sessions", ""))
        return (
            SessionInfo(
                id="s_one",
                filename="notebook.py",
                path="/workspace/notebook.py",
            ),
        )

    def invoke(
        self,
        session_id: str,
        operation: str,
        params: Mapping[str, object],
    ) -> dict[str, object]:
        del params
        self.calls.append((operation, session_id))
        if operation == "inspect":
            return cast(dict[str, object], _inspection())
        if operation == "release":
            if self.release_error is not None:
                raise self.release_error
            return {"released": True}
        assert operation == "capture"
        assets: list[JsonObject] = []
        if self.payload is not None:
            assets.append(
                {
                    "codec": "numpy.npy.v1",
                    "sha256": sha256_bytes(self.payload),
                    "size": len(self.payload),
                    "url": "/@file/native-return",
                }
            )
        return {
            "index": self.index.to_value(),
            "transfer": {
                "ticket": "ticket-1",
                "expires_at_ms": 4_000_000_000_000,
                "assets": assets,
            },
            "projection_cache": {"hits": 0, "misses": 1},
            "upstream_cache": {"hits": 2, "misses": 1},
            "fresh_child_timings": {
                "states": 1,
                "construction_seconds": 0.1,
                "upstream_execution_seconds": 0.2,
                "ui_application_seconds": 0.0,
                "projection_execution_seconds": 0.1,
                "cleanup_seconds": 0.1,
            },
        }

    def download_asset(
        self,
        session_id: str,
        url: str,
        maximum_bytes: int,
    ) -> bytes:
        self.calls.append(("download", session_id))
        assert url == "/@file/native-return"
        assert self.payload is not None
        assert maximum_bytes == len(self.payload)
        return self.payload


def _client(monkeypatch: pytest.MonkeyPatch, transport: _Transport) -> Client:
    monkeypatch.setattr(
        client_module,
        "HttpKernelTransport",
        lambda *args, **kwargs: transport,
    )
    return Client("http://127.0.0.1:2718")


def test_session_inspection_is_definition_centric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _Transport()
    with _client(monkeypatch, transport) as client:
        description = client.session().inspect()

    assert description.session_id == "s_one"
    assert description.document_sha256 == "a" * 64
    assert tuple(item.name for item in description.definitions) == ("answer",)
    assert description.definitions[0].value is None
    assert description.to_dict()["definitions"] == _inspection()["definitions"]


def test_capture_releases_transfer_before_committing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _npy()
    transport = _Transport(_index(asset=payload), payload=payload)
    output = tmp_path / "publication"
    with _client(monkeypatch, transport) as client:
        result = client.session().capture(spec=_spec(), output=output)

    assert result.mode == "capture"
    assert result.assets == 1
    assert result.projection_cache.misses == 1
    assert result.upstream_cache == client_module.CacheSummary(hits=2, misses=1)
    assert result.timings.capture_seconds >= 0
    assert [call[0] for call in transport.calls] == [
        "sessions",
        "capture",
        "download",
        "inspect",
        "release",
    ]
    assert open_publication(output).state("baseline").output("answer").asset_bytes() == payload


def test_capture_rejects_a_live_document_change_before_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ChangedDocumentTransport(_Transport):
        def invoke(
            self,
            session_id: str,
            operation: str,
            params: Mapping[str, object],
        ) -> dict[str, object]:
            response = super().invoke(session_id, operation, params)
            if operation == "inspect":
                response["document_sha256"] = "b" * 64
            return response

    transport = _ChangedDocumentTransport()
    output = tmp_path / "publication"
    with (
        _client(monkeypatch, transport) as client,
        pytest.raises(ExecutionError) as raised,
    ):
        client.session().capture(spec=_spec(), output=output)

    assert raised.value.code == "parent_document_changed"
    assert raised.value.details == {
        "before": "a" * 64,
        "after": "b" * 64,
    }
    assert [call[0] for call in transport.calls] == [
        "sessions",
        "capture",
        "inspect",
        "release",
    ]
    assert not output.exists()


def test_capture_release_failure_leaves_destination_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _Transport(
        release_error=BridgeError("session_error", "release failed"),
    )
    output = tmp_path / "publication"

    with (
        _client(monkeypatch, transport) as client,
        pytest.raises(SessionError, match="release failed"),
    ):
        client.session().capture(spec=_spec(), output=output)

    assert not output.exists()


def test_capture_rejects_extra_top_level_bridge_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ExtraFieldTransport(_Transport):
        def invoke(
            self,
            session_id: str,
            operation: str,
            params: Mapping[str, object],
        ) -> dict[str, object]:
            response = super().invoke(session_id, operation, params)
            if operation == "capture":
                response["extra"] = True
            return response

    output = tmp_path / "publication"
    with (
        _client(monkeypatch, _ExtraFieldTransport()) as client,
        pytest.raises(TransportError, match="capture response has invalid fields"),
    ):
        client.session().capture(spec=_spec(), output=output)

    assert not output.exists()


def test_destination_preflight_runs_before_remote_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _Transport()
    output = tmp_path / "publication"
    output.mkdir()

    with (
        _client(monkeypatch, transport) as client,
        pytest.raises(PublicationError) as raised,
    ):
        client.session().capture(spec=_spec(), output=output)

    assert raised.value.code == "destination_exists"
    assert [call[0] for call in transport.calls] == ["sessions"]


def test_client_selection_and_close_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _Transport()
    client = _client(monkeypatch, transport)
    assert client.session("s_one").id == "s_one"
    client.close()
    client.close()

    with pytest.raises(SessionError) as raised:
        client.sessions()
    assert raised.value.code == "client_closed"


def test_client_rejects_unknown_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with (
        _client(monkeypatch, _Transport()) as client,
        pytest.raises(SessionError) as raised,
    ):
        client.session("s_missing")

    assert raised.value.code == "session_not_found"
