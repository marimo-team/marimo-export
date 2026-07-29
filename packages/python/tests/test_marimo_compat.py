from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import marimo._runtime.executor.lifecycles.cached as cached_lifecycle
import marimo._save.loaders as native_loaders
import pytest
from marimo_export._marimo.compat import (
    _document_sha256,
    _track_upstream_cache,
    flush_native_caches,
    require_capabilities,
)


def test_attached_marimo_exposes_live_capture_capabilities() -> None:
    report = require_capabilities()

    assert report.version
    assert report.names == (
        "asset_transfer",
        "blob_asset",
        "cache_cells",
        "cell_cache_receipts",
        "child_sessions",
        "child_ui_updates",
        "definition_overrides",
        "setup_definition_overrides",
        "synthetic_projection_cells",
    )


def test_document_digest_uses_portable_cell_content() -> None:
    config = SimpleNamespace(asdict=lambda: {"column": None, "disabled": False, "hide_code": True})
    live = [
        SimpleNamespace(
            id="live-random-id",
            code="value = 1\n",
            name="",
            config=config,
        )
    ]
    reloaded = [
        SimpleNamespace(
            id="fresh-random-id",
            code="value = 1",
            name="_",
            config=config,
        )
    ]
    named = [
        SimpleNamespace(
            id="fresh-random-id",
            code="value = 1",
            name="named_cell",
            config=config,
        )
    ]

    assert _document_sha256(live) == _document_sha256(reloaded)
    assert _document_sha256(reloaded) != _document_sha256(named)


def test_native_cache_flush_uses_marimo_loader_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        native_loaders,
        "flush_active_caches",
        lambda: calls.append("flushed"),
    )

    flush_native_caches()

    assert calls == ["flushed"]


def test_upstream_cache_trackers_restore_native_binding_out_of_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_one = object()
    graph_two = object()
    outcomes = iter((True, False))

    def native(*args: Any, **kwargs: Any) -> SimpleNamespace:
        del args, kwargs
        return SimpleNamespace(hit=next(outcomes))

    monkeypatch.setattr(cached_lifecycle, "cache_attempt_from_hash", native)
    first = _track_upstream_cache(graph_one, frozenset())
    second = _track_upstream_cache(graph_two, frozenset())
    activity_one = first.__enter__()
    activity_two = second.__enter__()
    try:
        tracked = cast(Any, cached_lifecycle.cache_attempt_from_hash)
        tracked(None, graph_one, "one", {})
        tracked(None, graph_two, "two", {})

        first.__exit__(None, None, None)
        assert cached_lifecycle.cache_attempt_from_hash is tracked
        assert activity_one.hits == 1
        assert activity_two.misses == 1

        second.__exit__(None, None, None)
        assert cached_lifecycle.cache_attempt_from_hash is native
    finally:
        if cached_lifecycle.cache_attempt_from_hash is not native:
            second.__exit__(None, None, None)
            first.__exit__(None, None, None)
