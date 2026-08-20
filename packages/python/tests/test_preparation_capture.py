from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from marimo_export._json import JsonObject
from marimo_export._repository.preparation import (
    preparation_repository,
)
from marimo_export._services.capture_export import capture, capture_session
from marimo_export._services.export_artifacts import single_state_spec
from marimo_export.planning import PlannedState
from marimo_export.repository import (
    ExportRepository,
)
from marimo_export.result import CacheSummary
from marimo_export.spec import ExportSpec, OutputSpec
from marimo_export.wire import state_fingerprint
from preparation_test_support import (
    _BorrowedSession,
    _index,
    _plan_wire,
    _producer,
    _spec,
)


def test_single_state_capture_spec_preserves_the_complete_planned_vector() -> None:
    complete: JsonObject = {
        "interval": "1d",
        "symbols_selector": ["CRWV", "MSFT", "GOOGL"],
    }
    state = PlannedState(
        aliases=("ai_buildout",),
        inputs=complete,
        fingerprint=state_fingerprint(complete),
    )
    spec = ExportSpec(
        default_state="baseline",
        states={
            "baseline": {},
            "ai_buildout": {"symbols_selector": ["CRWV", "MSFT", "GOOGL"]},
            "weekly": {"interval": "1wk"},
        },
        outputs={"answer": OutputSpec.value("answer")},
    )

    captured = single_state_spec(spec, state)

    assert captured.default_state == "ai_buildout"
    assert captured.to_value()["states"] == {"ai_buildout": complete}


@pytest.mark.parametrize(
    ("timeout", "elapsed", "expected_timeout"),
    [(None, 29.0, 30.0), (45.0, 31.0, 45.0)],
)
def test_capture_session_timeout_bounds_waits_not_total_state_time(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    timeout: float | None,
    elapsed: float,
    expected_timeout: float,
) -> None:
    spec = _spec(states={"baseline": {"choice": "A"}})
    producer = _producer(tmp_path / "notebook.py")
    clock = [0.0]
    session = _BorrowedSession(
        _plan_wire(spec, producer),
        SimpleNamespace(
            index=_index(spec, producer),
            assets={},
            output_cache=CacheSummary(hits=0, misses=1),
            notebook_cache=CacheSummary(hits=0, misses=1),
            capture_seconds=elapsed,
        ),
        on_capture=lambda: clock.__setitem__(0, elapsed),
    )
    repository = ExportRepository.open(tmp_path / "repository")
    storage = preparation_repository(repository)
    native_reserve = storage.reserve_preparation
    observed_timeouts: list[float] = []

    @contextmanager
    def reserve(identity, **kwargs):
        observed_timeouts.append(kwargs["timeout"])
        with native_reserve(identity, **kwargs) as reservation:
            yield reservation

    monkeypatch.setattr(storage, "reserve_preparation", reserve)
    monkeypatch.setattr(
        "marimo_export._repository.preparation.time.monotonic",
        lambda: clock[0],
    )
    if timeout is None:
        prepared = capture_session(session, spec=spec, repository=repository)
    else:
        prepared = capture_session(
            session,
            spec=spec,
            repository=repository,
            timeout=timeout,
        )

    assert session.capture_calls == 1
    assert observed_timeouts == [expected_timeout]
    assert clock[0] == elapsed
    prepared.close()
    repository.close()


@pytest.mark.parametrize(
    ("timeout", "error"),
    [(True, TypeError), (0, ValueError), (float("inf"), ValueError)],
)
def test_capture_session_rejects_invalid_timeout(
    timeout: float,
    error: type[Exception],
) -> None:
    session = _BorrowedSession({}, SimpleNamespace())

    with pytest.raises(error, match="timeout"):
        capture_session(session, spec=_spec(), timeout=timeout)


@pytest.mark.parametrize(("timeout", "expected"), [(None, 30.0), (47.0, 47.0)])
def test_remote_capture_forwards_timeout_to_client_and_session(
    monkeypatch: pytest.MonkeyPatch,
    timeout: float | None,
    expected: float,
) -> None:
    observed: list[tuple[str, object]] = []
    prepared = object()

    class FakeClient:
        def __init__(self, _server: str, **kwargs) -> None:
            observed.append(("client", kwargs["timeout"]))

        def __enter__(self):
            return self

        def __exit__(self, *_error: object) -> None:
            return None

        def session(self, session_id: str):
            observed.append(("session", session_id))
            return object()

    def prepare_session(_session, **kwargs):
        observed.append(("capture", kwargs["timeout"]))
        return prepared

    monkeypatch.setattr("marimo_export._services.capture_export.Client", FakeClient)
    monkeypatch.setattr(
        "marimo_export._services.capture_export.capture_session",
        prepare_session,
    )
    if timeout is None:
        result = capture(
            "https://example.test",
            session="session-1",
            spec=_spec(),
        )
    else:
        result = capture(
            "https://example.test",
            session="session-1",
            spec=_spec(),
            timeout=timeout,
        )

    assert result is prepared
    assert observed == [
        ("client", expected),
        ("session", "session-1"),
        ("capture", expected),
    ]
