from __future__ import annotations

import json
import subprocess
import sys
import threading
from types import SimpleNamespace
from typing import Any, cast

import pytest
from marimo._runtime.executor.lifecycles import cached as cached_lifecycle
from marimo._runtime.executor.lifecycles.cached import CachedLifecycle
from marimo._runtime.runner.hooks import NotebookCellHooks
from marimo._save import encode
from marimo._save.hash import data_to_buffer
from marimo._save.loaders import PERSISTENT_LOADERS
from marimo._save.stubs.lazy_stub import LAZY_STUB_LOOKUP
from marimo._save.stubs.ui_element_stub import UIElementStub
from marimo_export._marimo.compat.cache.lifecycle import CompleteCachedLifecycle
from marimo_export._marimo.compat.cache.patch import managed_cache_compat
from marimo_export._marimo.compat.cache.probe import require_cache_capabilities
from marimo_export.errors import CompatibilityError
from marimo_export.integration import keep_cached_cells_compatible

_DATAFRAME = "polars.dataframe.frame.DataFrame"
_SERIES = "polars.series.series.Series"


def _attempt(*, definition: object = None, returned: object = None) -> Any:
    return SimpleNamespace(
        defs={} if definition is None else {"output": definition},
        meta={"return": returned},
        stateful_refs=set(),
    )


def _ui_marker() -> UIElementStub[Any, Any]:
    return object.__new__(UIElementStub)


def test_cached_ui_values_run_live_while_data_results_remain_lazy() -> None:
    release = keep_cached_cells_compatible()
    try:
        assert CachedLifecycle._restored_ui_defs(_attempt(definition=_ui_marker()), {})
        assert CachedLifecycle._restored_ui_defs(_attempt(returned=_ui_marker()), {})
        assert CachedLifecycle._restored_ui_defs(
            _attempt(definition={"nested": [_ui_marker()]}),
            {},
        )
        assert not CachedLifecycle._restored_ui_defs(
            _attempt(definition={"count": 18_259}, returned="ready"),
            {},
        )
    finally:
        release()


def test_cached_ui_detection_handles_recursive_containers() -> None:
    recursive: list[object] = []
    recursive.append(recursive)
    recursive.append(_ui_marker())
    release = keep_cached_cells_compatible()
    try:
        assert CachedLifecycle._restored_ui_defs(_attempt(definition=recursive), {})
    finally:
        release()


def test_cached_ui_detection_does_not_read_arbitrary_type_name_properties() -> None:
    class Hostile:
        @property
        def type_name(self) -> str:
            raise AssertionError("type_name was evaluated")

    release = keep_cached_cells_compatible()
    try:
        assert not CachedLifecycle._restored_ui_defs(
            _attempt(definition=Hostile()),
            {},
        )
    finally:
        release()


def test_polars_cache_uses_pickle_and_native_content_hashing() -> None:
    import polars as pl

    original_loaders = {name: LAZY_STUB_LOOKUP.get(name) for name in (_DATAFRAME, _SERIES)}
    original_tensor_buffer = encode._contiguous_tensor_bytes
    release = keep_cached_cells_compatible()
    try:
        assert LAZY_STUB_LOOKUP[_DATAFRAME] == "pickle"
        assert LAZY_STUB_LOOKUP[_SERIES] == "pickle"
        first = data_to_buffer(pl.DataFrame({"objectid": [1, 2]}))
        same = data_to_buffer(pl.DataFrame({"objectid": [1, 2]}))
        changed = data_to_buffer(pl.DataFrame({"objectid": [1, 3]}))
        assert first == same
        assert first != changed
    finally:
        release()
    assert {name: LAZY_STUB_LOOKUP.get(name) for name in (_DATAFRAME, _SERIES)} == (
        original_loaders
    )
    assert encode._contiguous_tensor_bytes is original_tensor_buffer


def test_host_cache_leases_restore_after_the_last_close() -> None:
    original = CachedLifecycle._restored_ui_defs
    first = keep_cached_cells_compatible()
    second = keep_cached_cells_compatible()

    first()
    assert CachedLifecycle._restored_ui_defs is not original
    assert CachedLifecycle._restored_ui_defs(_attempt(definition=_ui_marker()), {})

    second()
    assert CachedLifecycle._restored_ui_defs is original


def test_host_and_export_cache_leases_restore_independently() -> None:
    original = CachedLifecycle._restored_ui_defs
    hooks = NotebookCellHooks()

    host_release = keep_cached_cells_compatible()
    with managed_cache_compat(hooks):
        assert cached_lifecycle.CachedLifecycle is CompleteCachedLifecycle
        host_release()
        assert not cached_lifecycle.CachedLifecycle._restored_ui_defs(
            _attempt(definition=_ui_marker()),
            {},
        )
    assert CachedLifecycle._restored_ui_defs is original

    with managed_cache_compat(hooks):
        host_release = keep_cached_cells_compatible()
    assert cached_lifecycle.CachedLifecycle is CachedLifecycle
    assert CachedLifecycle._restored_ui_defs(_attempt(definition=_ui_marker()), {})
    host_release()
    assert CachedLifecycle._restored_ui_defs is original


def test_host_patch_conflict_releases_owned_state_before_raising() -> None:
    original_ui = CachedLifecycle._restored_ui_defs
    original_tensor = encode._contiguous_tensor_bytes
    original_loaders = {name: LAZY_STUB_LOOKUP.get(name) for name in (_DATAFRAME, _SERIES)}
    release = keep_cached_cells_compatible()

    def foreign_tensor(value: object) -> memoryview:
        del value
        return memoryview(b"foreign")

    cast(Any, encode)._contiguous_tensor_bytes = foreign_tensor
    try:
        with pytest.raises(CompatibilityError, match="another owner"):
            release()
        assert CachedLifecycle._restored_ui_defs is original_ui
        assert encode._contiguous_tensor_bytes is foreign_tensor
        assert {name: LAZY_STUB_LOOKUP.get(name) for name in (_DATAFRAME, _SERIES)} == (
            original_loaders
        )
    finally:
        cast(Any, encode)._contiguous_tensor_bytes = original_tensor

    retry = keep_cached_cells_compatible()
    retry()


def test_foreign_lifecycle_subclass_is_rejected_and_preserved() -> None:
    original_class = cached_lifecycle.CachedLifecycle
    original_ui = CachedLifecycle._restored_ui_defs
    original_tensor = encode._contiguous_tensor_bytes
    original_loaders = {name: LAZY_STUB_LOOKUP.get(name) for name in (_DATAFRAME, _SERIES)}
    release = keep_cached_cells_compatible()

    class ForeignLifecycle(CachedLifecycle):
        @staticmethod
        def _restored_ui_defs(attempt: object, glbls: dict[str, object]) -> bool:
            del attempt, glbls
            return False

    cast(Any, cached_lifecycle).CachedLifecycle = ForeignLifecycle
    try:
        with pytest.raises(CompatibilityError, match="another owner"):
            keep_cached_cells_compatible()
        with pytest.raises(CompatibilityError, match="another owner"):
            release()

        assert cached_lifecycle.CachedLifecycle is ForeignLifecycle
        assert CachedLifecycle._restored_ui_defs is original_ui
        assert encode._contiguous_tensor_bytes is original_tensor
        assert {name: LAZY_STUB_LOOKUP.get(name) for name in (_DATAFRAME, _SERIES)} == (
            original_loaders
        )
    finally:
        cast(Any, cached_lifecycle).CachedLifecycle = original_class


def test_foreign_same_value_polars_write_is_rejected_and_preserved() -> None:
    original_ui = CachedLifecycle._restored_ui_defs
    original_tensor = encode._contiguous_tensor_bytes
    original_loaders = {name: LAZY_STUB_LOOKUP.get(name) for name in (_DATAFRAME, _SERIES)}
    release = keep_cached_cells_compatible()
    foreign_pickle = "".join(("pick", "le"))
    LAZY_STUB_LOOKUP[_DATAFRAME] = foreign_pickle

    try:
        with pytest.raises(CompatibilityError, match="another owner"):
            release()

        assert CachedLifecycle._restored_ui_defs is original_ui
        assert encode._contiguous_tensor_bytes is original_tensor
        assert LAZY_STUB_LOOKUP[_DATAFRAME] is foreign_pickle
        assert LAZY_STUB_LOOKUP[_SERIES] == original_loaders[_SERIES]
    finally:
        original = original_loaders[_DATAFRAME]
        if original is None:
            LAZY_STUB_LOOKUP.pop(_DATAFRAME, None)
        else:
            LAZY_STUB_LOOKUP[_DATAFRAME] = original

    retry = keep_cached_cells_compatible()
    retry()


def test_concurrent_host_cache_leases_share_one_patch() -> None:
    original = CachedLifecycle._restored_ui_defs
    entered = threading.Barrier(9)
    release_workers = threading.Event()
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            release = keep_cached_cells_compatible()
            entered.wait(timeout=5)
            release_workers.wait(timeout=5)
            release()
        except BaseException as error:
            errors.append(error)

    workers = [threading.Thread(target=worker) for _ in range(8)]
    for worker_thread in workers:
        worker_thread.start()
    entered.wait(timeout=5)
    assert CachedLifecycle._restored_ui_defs(_attempt(definition=_ui_marker()), {})
    release_workers.set()
    for worker_thread in workers:
        worker_thread.join(timeout=5)
        assert not worker_thread.is_alive()

    assert errors == []
    assert CachedLifecycle._restored_ui_defs is original


def test_cache_probe_accepts_the_active_host_patch() -> None:
    release = keep_cached_cells_compatible()
    try:
        require_cache_capabilities()
    finally:
        release()


def test_public_host_capability_translates_missing_private_symbols(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_class = cached_lifecycle.CachedLifecycle
    original_attempt = cached_lifecycle.cache_attempt_from_hash
    original_loader = PERSISTENT_LOADERS["lazy"]
    original_ui = CachedLifecycle._restored_ui_defs
    original_tensor = encode._contiguous_tensor_bytes
    original_polars = {name: LAZY_STUB_LOOKUP.get(name) for name in (_DATAFRAME, _SERIES)}
    monkeypatch.delattr(CachedLifecycle, "setup")

    with pytest.raises(CompatibilityError) as raised:
        keep_cached_cells_compatible()

    assert raised.value.code == "marimo_incompatible"
    assert "CachedLifecycle.setup" in cast(list[str], raised.value.details["symbols"])
    assert cached_lifecycle.CachedLifecycle is original_class
    assert cached_lifecycle.cache_attempt_from_hash is original_attempt
    assert PERSISTENT_LOADERS["lazy"] is original_loader
    assert CachedLifecycle._restored_ui_defs is original_ui
    assert encode._contiguous_tensor_bytes is original_tensor
    assert {name: LAZY_STUB_LOOKUP.get(name) for name in (_DATAFRAME, _SERIES)} == (original_polars)


def test_host_cache_capability_import_defers_marimo_private_modules() -> None:
    script = """
import json
import sys
from marimo_export.integration import keep_cached_cells_compatible

assert keep_cached_cells_compatible is not None
print(json.dumps({
    "encode": "marimo._save.encode" in sys.modules,
    "lifecycle": "marimo._runtime.executor.lifecycles.cached" in sys.modules,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {"encode": False, "lifecycle": False}


def test_public_host_capability_translates_missing_module_level_lifecycle() -> None:
    script = """
import json
import marimo._runtime.executor.lifecycles.cached as cached_lifecycle
from marimo_export.errors import CompatibilityError
from marimo_export.integration import keep_cached_cells_compatible

del cached_lifecycle.CachedLifecycle
try:
    keep_cached_cells_compatible()
except CompatibilityError as error:
    print(json.dumps({"code": error.code, "symbols": error.details["symbols"]}))
else:
    raise AssertionError("missing CachedLifecycle was accepted")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result["code"] == "marimo_incompatible"
    assert "CachedLifecycle.setup" in result["symbols"]
