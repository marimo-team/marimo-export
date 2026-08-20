from __future__ import annotations

import asyncio
import hashlib
import pickle
from typing import Any, cast

import marimo_export._marimo.compat.cache.barrier as cache_barrier
import marimo_export._marimo.compat.cache.patch as cache_patch
import pytest
from marimo._runtime.runner.hooks import NotebookCellHooks
from marimo._save.cache import Cache as RuntimeCache
from marimo._save.loaders.lazy import LazyLoader
from marimo._save.signing import CacheSignatureError, CacheSigner
from marimo._save.stores.dict_store import DictStore
from marimo._save.stubs.lazy_stub import UnhashableStub
from marimo_export._marimo.compat.cache.attempts import (
    _rerun_unavailable_attempt,
)
from marimo_export._marimo.compat.cache.loader import SequentialLazyLoader
from marimo_export._marimo.compat.cache.patch import sequential_cache_loader
from marimo_export._marimo.compat.managed_kernel import kernel_lifespan


@pytest.mark.asyncio
async def test_sequential_cache_loader_owns_the_global_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marimo._save.loaders import PERSISTENT_LOADERS, DualLoader

    original = PERSISTENT_LOADERS["lazy"]
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()
    second_waiting = asyncio.Event()
    native_sleep = asyncio.sleep

    async def observed_sleep(delay: float) -> None:
        second_waiting.set()
        await native_sleep(delay)

    monkeypatch.setattr(cache_patch.asyncio, "sleep", observed_sleep)

    async def first() -> None:
        async with sequential_cache_loader():
            entry = PERSISTENT_LOADERS["lazy"]
            assert isinstance(entry, DualLoader)
            assert entry.native is SequentialLazyLoader
            first_entered.set()
            await release_first.wait()

    async def second() -> None:
        await first_entered.wait()
        async with sequential_cache_loader():
            second_entered.set()

    first_task = asyncio.create_task(first())
    second_task = asyncio.create_task(second())
    await asyncio.wait_for(first_entered.wait(), timeout=1)
    await asyncio.wait_for(second_waiting.wait(), timeout=1)
    assert not second_entered.is_set()
    release_first.set()
    await asyncio.gather(first_task, second_task)

    assert PERSISTENT_LOADERS["lazy"] is original


@pytest.mark.asyncio
async def test_sequential_cache_loader_releases_lock_after_setup_failure() -> None:
    from marimo._save.loaders import PERSISTENT_LOADERS

    original = PERSISTENT_LOADERS.pop("lazy")
    try:
        with pytest.raises(KeyError):
            async with sequential_cache_loader():
                pass
    finally:
        PERSISTENT_LOADERS["lazy"] = original

    async def load_after_failure() -> None:
        async with sequential_cache_loader():
            assert PERSISTENT_LOADERS["lazy"] is not original

    await asyncio.wait_for(load_after_failure(), timeout=1)

    assert PERSISTENT_LOADERS["lazy"] is original


def test_managed_cache_installation_is_idempotent() -> None:
    hooks = NotebookCellHooks()

    with cache_patch.managed_cache_compat(hooks):
        pass
    with cache_patch.managed_cache_compat(hooks):
        pass

    assert list(hooks.post_execution_hooks).count(cache_barrier._flush_cache_writes) == 1


def test_unavailable_cache_hit_becomes_a_reported_miss() -> None:
    attempt = RuntimeCache(
        defs={"value": UnhashableStub(var_name="value", error_msg="unavailable")},
        hash="a" * 64,
        cache_type="Pure",
        stateful_refs=set(),
        hit=True,
        meta={},
    )

    retry = _rerun_unavailable_attempt(attempt)

    assert not retry.hit
    assert retry.defs == {"value": None}


def test_cache_write_barrier_precedes_dependent_post_execution_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marimo._save.loaders as loaders
    from marimo._runtime.runner.hooks import Priority

    events: list[str] = []
    hooks = NotebookCellHooks()
    monkeypatch.setattr(loaders, "flush_active_caches", lambda: events.append("writes-finished"))
    cache_barrier.add_cache_write_barrier(hooks)
    hooks.add_post_execution(
        lambda cell, context, result: events.append("dependent-hash"),
        Priority.NORMAL,
    )

    for hook in hooks.post_execution_hooks:
        cast(Any, hook)(None, None, None)

    assert events == ["writes-finished", "dependent-hash"]


def test_sequential_loader_matches_native_cache_trust_precedence() -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    signer = CacheSigner(private_key=Ed25519PrivateKey.generate())
    valid = pickle.dumps({"answer": 42})
    cases = (
        ({"value.pickle": valid}, {"value.pickle": hashlib.sha256(valid).hexdigest()}, signer),
        ({"value.pickle": valid}, {"value.pickle": "0" * 64}, signer),
        ({}, {}, signer),
        ({}, {}, None),
        (
            {"value.pickle": b"not a pickle"},
            {"value.pickle": hashlib.sha256(b"not a pickle").hexdigest()},
            signer,
        ),
    )

    def outcome(
        loader_type: type[LazyLoader],
        values: dict[str, bytes],
        hashes: dict[str, str],
        effective_signer: CacheSigner | None,
    ) -> tuple[str, object]:
        store = DictStore()
        for key, value in values.items():
            store.put(key, value)
        loader = loader_type(
            name=f"parity-{loader_type.__name__}-{len(values)}-{len(hashes)}",
            store=store,
            signer=None,
            mode="off",
        )
        try:
            return (
                "value",
                loader._read_blobs(
                    {"value.pickle"},
                    {},
                    "value.pickle",
                    None,
                    hashes,
                    effective_signer,
                ),
            )
        except Exception as error:
            return ("error", type(error))

    for values, hashes, effective_signer in cases:
        native = outcome(LazyLoader, values, hashes, effective_signer)
        sequential = outcome(SequentialLazyLoader, values, hashes, effective_signer)
        assert sequential == native
    assert outcome(
        SequentialLazyLoader,
        {"value.pickle": valid},
        {"value.pickle": "0" * 64},
        signer,
    ) == ("error", CacheSignatureError)


@pytest.mark.asyncio
async def test_managed_kernel_lifespan_is_dormant_without_managed_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marimo._save.loaders import PERSISTENT_LOADERS

    monkeypatch.delenv("MARIMO_EXPORT_MANAGED_CACHE_COMPAT", raising=False)
    original = PERSISTENT_LOADERS["lazy"]

    async with kernel_lifespan(None):
        assert PERSISTENT_LOADERS["lazy"] is original

    assert PERSISTENT_LOADERS["lazy"] is original
