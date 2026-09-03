from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from marimo._runtime.executor.lifecycles import Skip
from marimo._runtime.executor.lifecycles import cached as cached_lifecycle
from marimo._runtime.executor.lifecycles.cached import CachedLifecycle
from marimo._runtime.runner.hooks import NotebookCellHooks
from marimo._runtime.runner.result import RunResult
from marimo._runtime.state import State
from marimo._save.cache import Cache
from marimo._save.loaders import PERSISTENT_LOADERS
from marimo._save.stubs.lazy_stub import UnhashableStub
from marimo_export._marimo.compat.cache.attempts import (
    track_managed_parent_cache,
    track_notebook_cache,
)
from marimo_export._marimo.compat.cache.lifecycle import (
    CompleteCachedLifecycle,
    _restored_session_state,
)
from marimo_export._marimo.compat.cache.loader import SequentialLazyLoader
from marimo_export._marimo.compat.cache.patch import _PATCHES, managed_cache_compat
from marimo_export._marimo.compat.cache.probe import require_cache_capabilities
from marimo_export.errors import CompatibilityError


def test_overlapping_managed_cache_leases_restore_after_the_last_close() -> None:
    original_loader = PERSISTENT_LOADERS["lazy"]
    original_lifecycle = cached_lifecycle.CachedLifecycle
    hooks = NotebookCellHooks()
    first = managed_cache_compat(hooks)
    second = managed_cache_compat(hooks)

    first.__enter__()
    second.__enter__()
    first.__exit__(None, None, None)
    assert PERSISTENT_LOADERS["lazy"] is not original_loader
    assert cached_lifecycle.CachedLifecycle is CompleteCachedLifecycle

    second.__exit__(None, None, None)
    assert PERSISTENT_LOADERS["lazy"] is original_loader
    assert cached_lifecycle.CachedLifecycle is original_lifecycle


def test_tracking_one_graph_does_not_change_an_untracked_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracked_graph = object()
    untracked_graph = object()
    native_attempt = Cache(
        defs={"value": UnhashableStub(var_name="value", error_msg="unavailable")},
        hash="a" * 64,
        cache_type="Pure",
        stateful_refs=set(),
        hit=True,
        meta={},
    )
    monkeypatch.setattr(
        cached_lifecycle,
        "cache_attempt_from_hash",
        lambda *args, **kwargs: native_attempt,
    )

    hooks = NotebookCellHooks()
    with managed_cache_compat(hooks), track_notebook_cache(tracked_graph, frozenset()):
        observed = cast(Any, cached_lifecycle.cache_attempt_from_hash)(
            None,
            untracked_graph,
            "cell",
            {},
        )

    assert observed is native_attempt
    assert observed.hit


def test_sequential_loader_remains_the_native_registry_entry_during_overlap() -> None:
    hooks = NotebookCellHooks()

    with managed_cache_compat(hooks):
        entry = PERSISTENT_LOADERS["lazy"]
        native = getattr(entry, "native", entry)
        assert native is SequentialLazyLoader


def test_cache_probe_accepts_the_owned_active_patch() -> None:
    hooks = NotebookCellHooks()

    with managed_cache_compat(hooks):
        require_cache_capabilities()


def test_patch_conflict_releases_ownership_and_restores_owned_globals() -> None:
    original_loader = PERSISTENT_LOADERS["lazy"]
    original_lifecycle = cached_lifecycle.CachedLifecycle
    original_attempt = cached_lifecycle.cache_attempt_from_hash
    handle = _PATCHES.open()
    installed_attempt = cached_lifecycle.cache_attempt_from_hash

    def foreign_attempt(*args: object, **kwargs: object) -> None:
        del args, kwargs

    cast(Any, cached_lifecycle).cache_attempt_from_hash = foreign_attempt

    try:
        with pytest.raises(CompatibilityError, match="another owner"):
            handle.close()

        assert PERSISTENT_LOADERS["lazy"] is original_loader
        assert cached_lifecycle.CachedLifecycle is original_lifecycle
        assert cached_lifecycle.cache_attempt_from_hash is foreign_attempt
    finally:
        if PERSISTENT_LOADERS["lazy"] is not original_loader:
            cast(Any, cached_lifecycle).cache_attempt_from_hash = installed_attempt
            handle.close()
        cast(Any, cached_lifecycle).cache_attempt_from_hash = original_attempt


def test_complete_lifecycle_leaves_untracked_unavailable_hits_native(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = object()
    attempt = Cache(
        defs={"value": UnhashableStub(var_name="value", error_msg="unavailable")},
        hash="a" * 64,
        cache_type="Pure",
        stateful_refs=set(),
        hit=True,
        meta={},
    )
    decision = Skip(result=RunResult(output=None, exception=None))
    lifecycle = cast(Any, object.__new__(CompleteCachedLifecycle))
    lifecycle._graph = graph
    lifecycle._attempts = {"cell": attempt}
    lifecycle._exec_starts = {}
    monkeypatch.setattr(CachedLifecycle, "setup", lambda self, cell, glbls: decision)

    observed = lifecycle.setup(SimpleNamespace(cell_id="cell"), {})

    assert observed is decision
    assert lifecycle._attempts["cell"] is attempt


def test_complete_lifecycle_recreates_session_bound_state() -> None:
    state = State(1)
    attempt = Cache(
        defs={"get_selected": state},
        hash="a" * 64,
        cache_type="Pure",
        stateful_refs=set(),
        hit=True,
        meta={},
    )

    assert _restored_session_state(
        attempt,
        {"get_selected": state},
    )


def test_complete_lifecycle_reruns_unavailable_hits_in_managed_parent_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = object()
    attempt = Cache(
        defs={"value": UnhashableStub(var_name="value", error_msg="unavailable")},
        hash="a" * 64,
        cache_type="Pure",
        stateful_refs=set(),
        hit=True,
        meta={},
    )
    decision = Skip(result=RunResult(output=None, exception=None))
    lifecycle = cast(Any, object.__new__(CompleteCachedLifecycle))
    lifecycle._graph = graph
    lifecycle._attempts = {"cell": attempt}
    lifecycle._exec_starts = {}
    monkeypatch.setattr(CachedLifecycle, "setup", lambda self, cell, glbls: decision)

    with track_managed_parent_cache(graph):
        observed = lifecycle.setup(SimpleNamespace(cell_id="cell"), {})

    assert observed is None
    assert not lifecycle._attempts["cell"].hit
