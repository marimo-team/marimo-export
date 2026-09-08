from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from types import SimpleNamespace
from typing import cast

import pytest
from marimo_export._publication import PublicationControllerState
from marimo_export.prepared import PreparedExport
from marimo_export.publication import (
    PreparedPublication,
    PreparedPublicationCandidate,
    PreparedPublicationController,
)
from marimo_export.repository import ExportRepository, RepositoryError


class _Repository:
    def __init__(self) -> None:
        self.revision = 0
        self.closed = False

    def observation_revision(self, plan: object) -> int:
        return self.revision

    def close(self) -> None:
        self.closed = True


class _Asset:
    def __init__(self, value: bytes) -> None:
        self.value = value
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Prepared:
    def __init__(self, instance: int, observation_revision: int) -> None:
        self.identity = f"{instance:064x}"
        self.plan = SimpleNamespace(
            producer_sha256="a" * 64,
            observation_revision=observation_revision,
        )
        self.closed = False
        self.close_calls = 0
        self.closed_event = threading.Event()
        self.assets: list[_Asset] = []

    def manifest(
        self,
        export_url: str,
        *,
        state: object = None,
        refresh_interval_ms: int | None = None,
    ) -> dict[str, object]:
        return {
            "schema": "marimo-export.prepared.v1",
            "instance": self.identity,
            "export_url": export_url,
            "inputs": state or {},
            "state_fingerprint": "f" * 64,
            **(
                {"refresh_interval_ms": refresh_interval_ms}
                if refresh_interval_ms is not None
                else {}
            ),
        }

    def asset(self, relative: str) -> _Asset:
        if self.closed or relative != "index.json":
            raise RepositoryError("asset unavailable")
        asset = _Asset(self.identity.encode())
        self.assets.append(asset)
        return asset

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True
        self.closed_event.set()


def _candidate(
    prepared: _Prepared,
    metadata: str,
) -> PreparedPublicationCandidate[str]:
    return PreparedPublicationCandidate(
        prepared=cast(PreparedExport, prepared),
        metadata=metadata,
    )


def _controller(
    repository: _Repository | None = None,
    *,
    route_grace_seconds: float = 60,
) -> PreparedPublicationController[tuple[str, str], str]:
    return PreparedPublicationController(
        repository=(None if repository is None else cast(ExportRepository, repository)),
        supersession_key=lambda key: key[0],
        route_key=lambda key: key[0],
        route_grace_seconds=route_grace_seconds,
    )


def _observations_changed(repository: ExportRepository, publication: PreparedPublication) -> bool:
    return repository.observation_revision(publication.plan) > publication.plan.observation_revision


def test_publication_values_come_from_the_controller() -> None:
    with pytest.raises(TypeError, match="returned by PreparedPublicationController"):
        PreparedPublication()


def test_controller_lazily_owns_default_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _Repository()
    prepared = _Prepared(1, 0)
    monkeypatch.setattr(
        ExportRepository,
        "open",
        classmethod(lambda _cls: cast(ExportRepository, repository)),
    )
    controller = _controller()
    assert not controller.active

    async def scenario() -> None:
        publication = await controller.prepare(
            ("dashboard", "first"),
            lambda selected, _cancelled: (
                _candidate(prepared, "first")
                if selected is repository
                else pytest.fail("unexpected repository")
            ),
        )
        assert controller.active
        assert publication.identity == prepared.identity
        assert publication.metadata == "first"
        assert publication.manifest("./export/")["instance"] == prepared.identity
        await controller.close()
        assert not controller.active

    asyncio.run(scenario())

    assert prepared.closed
    assert repository.closed


def test_new_key_in_group_supersedes_blocking_preparation() -> None:
    repository = _Repository()
    controller = _controller(repository)
    started = threading.Event()

    def blocked(
        _repository: ExportRepository,
        cancelled: Callable[[], bool],
    ) -> PreparedPublicationCandidate[str]:
        started.set()
        while not cancelled():
            threading.Event().wait(0.005)
        raise asyncio.CancelledError

    async def scenario() -> None:
        older = asyncio.create_task(controller.prepare(("dashboard", "first"), blocked))
        assert await asyncio.to_thread(started.wait, 2)
        prepared = _Prepared(2, 0)
        newest = await controller.prepare(
            ("dashboard", "second"),
            lambda _repository, _cancelled: _candidate(prepared, "second"),
        )
        with pytest.raises(asyncio.CancelledError):
            await older
        assert controller.current(("dashboard", "first")) is None
        assert controller.current(("dashboard", "second")) is newest
        await controller.close()

    asyncio.run(scenario())
    assert not repository.closed


def test_failed_replacement_preserves_last_good_publication() -> None:
    repository = _Repository()
    controller = _controller(repository)
    prepared = _Prepared(3, 0)

    async def scenario() -> None:
        selected = await controller.prepare(
            ("dashboard", "first"),
            lambda _repository, _cancelled: _candidate(prepared, "first"),
        )

        def fail(
            _repository: ExportRepository,
            _cancelled: Callable[[], bool],
        ) -> PreparedPublicationCandidate[str]:
            raise RuntimeError("prepare failed")

        with pytest.raises(RuntimeError, match="prepare failed"):
            await controller.prepare(("dashboard", "first"), fail)
        assert controller.current(("dashboard", "first")) is selected
        assert not prepared.closed
        await controller.close()

    asyncio.run(scenario())
    assert prepared.closed


def test_replaced_publication_retains_route_and_independent_asset_lease() -> None:
    repository = _Repository()
    controller = _controller(repository)
    first = _Prepared(4, 0)
    second = _Prepared(5, 0)

    async def scenario() -> None:
        await controller.prepare(
            ("dashboard", "first"),
            lambda _repository, _cancelled: _candidate(first, "first"),
        )
        await controller.prepare(
            ("dashboard", "second"),
            lambda _repository, _cancelled: _candidate(second, "second"),
        )
        assert not first.closed
        asset = controller.asset("dashboard", first.identity, "index.json")
        assert asset is not None
        controller.release(("dashboard", "second"))
        assert first.closed
        assert second.closed
        assert not cast(_Asset, asset).closed
        asset.close()
        await controller.close()

    asyncio.run(scenario())


def test_same_identity_replacement_retains_previous_route() -> None:
    repository = _Repository()
    controller: PreparedPublicationController[tuple[str, str, str], str] = (
        PreparedPublicationController(
            repository=cast(ExportRepository, repository),
            supersession_key=lambda key: key[0],
            route_key=lambda key: key[1],
            route_grace_seconds=60,
        )
    )
    first = _Prepared(5, 0)
    second = _Prepared(5, 0)

    async def scenario() -> None:
        await controller.prepare(
            ("dashboard", "old-route", "first"),
            lambda _repository, _cancelled: _candidate(first, "first"),
        )
        await controller.prepare(
            ("dashboard", "new-route", "second"),
            lambda _repository, _cancelled: _candidate(second, "second"),
        )
        assert not first.closed
        old_asset = controller.asset("old-route", first.identity, "index.json")
        new_asset = controller.asset("new-route", second.identity, "index.json")
        assert old_asset is not None
        assert new_asset is not None
        old_asset.close()
        new_asset.close()
        controller.release(("dashboard", "new-route", "second"))
        assert first.closed
        assert second.closed
        await controller.close()

    asyncio.run(scenario())


def test_zero_route_grace_closes_replaced_publication() -> None:
    repository = _Repository()
    controller = _controller(repository, route_grace_seconds=0)
    first = _Prepared(8, 0)
    second = _Prepared(9, 0)

    async def scenario() -> None:
        await controller.prepare(
            ("dashboard", "first"),
            lambda _repository, _cancelled: _candidate(first, "first"),
        )
        await controller.prepare(
            ("dashboard", "second"),
            lambda _repository, _cancelled: _candidate(second, "second"),
        )
        assert first.closed
        assert controller.asset("dashboard", first.identity, "index.json") is None
        await controller.close()

    asyncio.run(scenario())


def test_retired_publication_expires_without_followup_request() -> None:
    repository = _Repository()
    controller = _controller(repository, route_grace_seconds=0.01)
    first = _Prepared(10, 0)
    second = _Prepared(11, 0)

    async def scenario() -> None:
        await controller.prepare(
            ("dashboard", "first"),
            lambda _repository, _cancelled: _candidate(first, "first"),
        )
        await controller.prepare(
            ("dashboard", "second"),
            lambda _repository, _cancelled: _candidate(second, "second"),
        )
        assert await asyncio.to_thread(first.closed_event.wait, 2)
        assert first.closed
        assert first.close_calls == 1
        await controller.close()

    asyncio.run(scenario())


def test_close_cancels_retirement_deadline() -> None:
    repository = _Repository()
    controller = _controller(repository, route_grace_seconds=0.01)
    first = _Prepared(12, 0)
    second = _Prepared(13, 0)

    async def scenario() -> None:
        await controller.prepare(
            ("dashboard", "first"),
            lambda _repository, _cancelled: _candidate(first, "first"),
        )
        await controller.prepare(
            ("dashboard", "second"),
            lambda _repository, _cancelled: _candidate(second, "second"),
        )
        deadline = asyncio.Event()
        asyncio.get_running_loop().call_later(0.01, deadline.set)
        await controller.close()
        await asyncio.wait_for(deadline.wait(), 2)
        assert first.close_calls == 1
        assert second.close_calls == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("foreground_fails", [False, True])
def test_observation_refresh_preserves_newer_preparation_intent(
    monkeypatch: pytest.MonkeyPatch,
    foreground_fails: bool,
) -> None:
    repository = _Repository()
    revision_started = threading.Event()
    revision_resume = threading.Event()
    foreground_started = threading.Event()
    foreground_resume = threading.Event()
    controller = _controller(repository)

    def revision(_plan: object) -> int:
        revision_started.set()
        assert revision_resume.wait(5)
        return 1

    monkeypatch.setattr(repository, "observation_revision", revision)

    def original(
        _repository: ExportRepository,
        _cancelled: Callable[[], bool],
    ) -> PreparedPublicationCandidate[str]:
        return _candidate(_Prepared(14, 0), "original")

    def foreground(
        _repository: ExportRepository,
        _cancelled: Callable[[], bool],
    ) -> PreparedPublicationCandidate[str]:
        foreground_started.set()
        if foreground_fails:
            raise RuntimeError("foreground failed")
        assert foreground_resume.wait(5)
        return _candidate(_Prepared(15, 1), "foreground")

    async def scenario() -> None:
        refresh_finished = asyncio.Event()
        refresh = PublicationControllerState._refresh_if_stale

        async def tracked_refresh(self, selected, desired, should_refresh) -> None:
            try:
                await refresh(self, selected, desired, should_refresh)
            finally:
                refresh_finished.set()

        monkeypatch.setattr(PublicationControllerState, "_refresh_if_stale", tracked_refresh)
        selected = await controller.prepare(("dashboard", "original"), original)
        newer: asyncio.Task | None = None
        try:
            controller.poll(("dashboard", "original"), should_refresh=_observations_changed)
            assert await asyncio.to_thread(revision_started.wait, 5)
            newer = asyncio.create_task(controller.prepare(("dashboard", "newer"), foreground))
            assert await asyncio.to_thread(foreground_started.wait, 5)
            if foreground_fails:
                with pytest.raises(RuntimeError, match="foreground failed"):
                    await newer
            revision_resume.set()
            await asyncio.wait_for(refresh_finished.wait(), 5)
            foreground_resume.set()
            if foreground_fails:
                assert controller.current(("dashboard", "original")) is selected
            else:
                publication = await newer
                assert controller.current(("dashboard", "newer")) is publication
        finally:
            revision_resume.set()
            foreground_resume.set()
            if newer is not None:
                await asyncio.gather(newer, return_exceptions=True)
            await controller.close()

    asyncio.run(scenario())


def test_repository_open_allows_event_loop_progress_and_settles_before_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _Repository()
    opening = threading.Event()
    resume = threading.Event()
    prepared = _Prepared(16, 0)

    def open_repository() -> ExportRepository:
        opening.set()
        assert resume.wait(5)
        return cast(ExportRepository, repository)

    monkeypatch.setattr(ExportRepository, "open", open_repository)
    controller = _controller()

    async def scenario() -> None:
        preparation = asyncio.create_task(
            controller.prepare(
                ("dashboard", "first"),
                lambda _repository, _cancelled: _candidate(prepared, "first"),
            )
        )
        closing: asyncio.Task[None] | None = None
        try:
            assert await asyncio.to_thread(opening.wait, 5)
            assert not preparation.done()
            closing = asyncio.create_task(controller.close())
            await asyncio.sleep(0)
            assert not closing.done()
            resume.set()
            await closing
            with pytest.raises(asyncio.CancelledError):
                await preparation
            assert repository.closed
            assert prepared.closed
            assert not controller.active
        finally:
            resume.set()
            await asyncio.gather(preparation, return_exceptions=True)
            await controller.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_route_grace_requires_a_finite_nonnegative_duration(value: float) -> None:
    with pytest.raises(ValueError, match="finite nonnegative"):
        PreparedPublicationController(route_grace_seconds=value)


@pytest.mark.parametrize("value", [True, "60"])
def test_route_grace_requires_a_number(value: object) -> None:
    with pytest.raises(TypeError, match="number"):
        PreparedPublicationController(route_grace_seconds=cast(float, value))


def test_rejected_application_admission_preserves_last_good_publication() -> None:
    controller = _controller(_Repository())
    first, rejected = _Prepared(20, 0), _Prepared(21, 0)

    async def reject(_candidate: PreparedPublicationCandidate[str]) -> None:
        raise ValueError("application revision changed")

    async def scenario() -> None:
        original = await controller.prepare(("report", "old"), lambda *_: _candidate(first, "old"))
        try:
            with pytest.raises(ValueError, match="application revision changed"):
                await controller.prepare(
                    ("report", "new"), lambda *_: _candidate(rejected, "new"), admit=reject
                )
            assert controller.current(("report", "old")) is original
            assert not first.closed
            assert rejected.closed
        finally:
            await controller.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["release", "replace", "close", "cancel"])
def test_waiting_admission_cannot_commit_after_application_supersession(action: str) -> None:
    controller = _controller(_Repository())
    waiting, replacement = _Prepared(22, 0), _Prepared(23, 0)

    async def scenario() -> None:
        entered, resume = asyncio.Event(), asyncio.Event()

        async def admit(_candidate: PreparedPublicationCandidate[str]) -> None:
            entered.set()
            await resume.wait()

        pending = asyncio.create_task(
            controller.prepare(
                ("report", "waiting"), lambda *_: _candidate(waiting, "waiting"), admit=admit
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if action == "release":
                controller.release(("report", "waiting"))
            elif action == "close":
                await asyncio.wait_for(controller.close(), 5)
            elif action == "cancel":
                pending.cancel()
            else:
                await controller.prepare(
                    ("report", "replacement"), lambda *_: _candidate(replacement, "replacement")
                )
            resume.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert waiting.closed
            assert controller.current(("report", "waiting")) is None
            if action == "replace":
                assert controller.current(("report", "replacement")) is not None
        finally:
            resume.set()
            await asyncio.gather(pending, return_exceptions=True)
            await controller.close()

    asyncio.run(scenario())


def test_application_file_revision_refreshes_with_retained_admission(tmp_path) -> None:
    revision = tmp_path / "revision.txt"
    revision.write_text("first")
    controller = _controller(_Repository())
    prepared = iter((_Prepared(24, 0), _Prepared(25, 0)))
    owner_thread = threading.get_ident()

    def prepare(*_args):
        return _candidate(next(prepared), revision.read_text())

    def stale(_repository, publication):
        assert threading.get_ident() != owner_thread
        return revision.read_text() != publication.metadata

    async def scenario() -> None:
        admitted = asyncio.Event()

        async def admit(candidate):
            if candidate.metadata == "second":
                admitted.set()

        try:
            initial = await controller.prepare(("report", "main"), prepare, admit=admit)
            revision.write_text("second")
            assert controller.poll(("report", "main"), should_refresh=stale) is initial
            await asyncio.wait_for(admitted.wait(), 5)

            async def committed() -> None:
                while True:
                    current = controller.current(("report", "main"))
                    if current is not None and current.metadata == "second":
                        return
                    await asyncio.sleep(0)

            await asyncio.wait_for(committed(), 5)
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_released_publication_supersedes_pending_refresh_predicate() -> None:
    controller = _controller(_Repository())
    started, resume = threading.Event(), threading.Event()
    calls = 0

    def prepare(*_args):
        nonlocal calls
        calls += 1
        return _candidate(_Prepared(26, 0), "report")

    def stale(_repository, _publication):
        started.set()
        assert resume.wait(5)
        return True

    async def scenario() -> None:
        try:
            await controller.prepare(("report", "main"), prepare)
            controller.poll(("report", "main"), should_refresh=stale)
            assert await asyncio.to_thread(started.wait, 5)
            controller.release(("report", "main"))
            resume.set()
        finally:
            resume.set()
            await controller.close()
        assert calls == 1
        assert controller.current(("report", "main")) is None

    asyncio.run(scenario())
