from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest
from marimo_export import ExportSpec, OutputSpec
from marimo_export import client as client_module
from marimo_export._remote import SessionInfo
from marimo_export.client import Client, Session
from marimo_export.errors import SessionError


def _spec() -> ExportSpec:
    return ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={"answer": OutputSpec.json("answer")},
    )


class _Transport:
    def __init__(self, sessions: tuple[SessionInfo, ...]) -> None:
        self._sessions = sessions

    def list_sessions(self) -> tuple[SessionInfo, ...]:
        return self._sessions

    def invoke(
        self,
        session_id: str,
        operation: str,
        params: Mapping[str, object],
    ) -> dict[str, object]:
        raise AssertionError((session_id, operation, params))

    def download_asset(self, session_id: str, url: str, maximum_bytes: int) -> bytes:
        raise AssertionError((session_id, url, maximum_bytes))


def _client(monkeypatch: pytest.MonkeyPatch, sessions: tuple[SessionInfo, ...]) -> Client:
    monkeypatch.setattr(
        client_module, "HttpKernelTransport", lambda *_args, **_kwargs: _Transport(sessions)
    )
    return Client("http://127.0.0.1:2718")


def test_client_selects_explicit_or_unique_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = (
        SessionInfo(id="first", filename="first.py", path="/first.py"),
        SessionInfo(id="second", filename="second.py", path="/second.py"),
    )
    with _client(monkeypatch, sessions) as client:
        assert client.session("second").filename == "second.py"
        with pytest.raises(SessionError, match="more than one"):
            client.session()
        with pytest.raises(SessionError, match="was not found"):
            client.session("missing")

    with _client(monkeypatch, sessions[:1]) as client:
        assert client.session().id == "first"
        client.close()
        with pytest.raises(SessionError, match="closed"):
            client.sessions()


def test_session_capture_delegates_to_preparation_without_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = object()
    seen: list[object] = []
    sessions = (SessionInfo(id="session", filename="notebook.py", path="/notebook.py"),)

    def capture_session(session: Session, **kwargs):
        seen.extend((session, kwargs))
        return expected

    monkeypatch.setattr(
        "marimo_export._services.capture_export.capture_session",
        capture_session,
    )
    with _client(monkeypatch, sessions) as client:
        session = client.session("session")
        result = session.capture(spec=_spec(), timeout=47)

    assert result is expected
    assert seen[0] is session
    kwargs = cast(dict[str, object], seen[1])
    assert kwargs["spec"] == _spec()
    assert kwargs["timeout"] == 47
    assert "output" not in kwargs


def test_session_plan_delegates_without_capturing(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = object()
    seen: list[object] = []
    sessions = (SessionInfo(id="session", filename="notebook.py", path="/notebook.py"),)

    def plan_session(session: Session, **kwargs):
        seen.extend((session, kwargs))
        return expected

    monkeypatch.setattr(
        "marimo_export._services.capture_export.plan_session",
        plan_session,
    )
    with _client(monkeypatch, sessions) as client:
        session = client.session("session")
        result = session.plan(spec=_spec())

    assert result is expected
    assert seen[0] is session
    kwargs = cast(dict[str, object], seen[1])
    assert kwargs["spec"] == _spec()


def test_root_capture_requires_an_explicit_session() -> None:
    with pytest.raises(TypeError, match="session"):
        client_module.capture(
            "http://127.0.0.1:2718",
            session="",
            spec=_spec(),
        )


def test_root_capture_delegates_to_shared_service(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = object()
    seen: list[object] = []

    def capture(server: str, **kwargs):
        seen.extend((server, kwargs))
        return expected

    monkeypatch.setattr("marimo_export._services.capture_export.capture", capture)

    result = client_module.capture(
        "https://example.test",
        session="session-1",
        spec=_spec(),
    )

    assert result is expected
    assert seen[0] == "https://example.test"
    kwargs = cast(dict[str, object], seen[1])
    assert kwargs["session"] == "session-1"
    assert "output" not in kwargs
