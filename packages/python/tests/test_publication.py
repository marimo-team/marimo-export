from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

import pytest
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
        assert cast(Any, plan).producer_sha256 == "a" * 64
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


def test_poll_refreshes_after_observation_revision_advances() -> None:
    repository = _Repository()
    controller = _controller(repository)
    first = _Prepared(6, 0)
    second = _Prepared(7, 1)
    candidates = iter((_candidate(first, "first"), _candidate(second, "second")))

    def prepare(
        _repository: ExportRepository,
        _cancelled: Callable[[], bool],
    ) -> PreparedPublicationCandidate[str]:
        return next(candidates)

    async def scenario() -> None:
        selected = await controller.prepare(("dashboard", "first"), prepare)
        repository.revision = 1
        assert controller.poll(("dashboard", "first")) is selected
        for _ in range(100):
            current = controller.current(("dashboard", "first"))
            if current is not None and current.identity == second.identity:
                break
            await asyncio.sleep(0.005)
        else:
            pytest.fail("observation refresh did not commit")
        assert not first.closed
        asset = controller.asset("dashboard", first.identity, "index.json")
        assert asset is not None
        asset.close()
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
        assert not first.closed
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
        await controller.close()
        await asyncio.sleep(0.03)
        assert first.close_calls == 1
        assert second.close_calls == 1

    asyncio.run(scenario())
