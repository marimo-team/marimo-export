from __future__ import annotations

import asyncio
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, cast

import pytest
from marimo._save.cache import Cache
from marimo._save.hash import HashKey
from marimo._save.loaders.lazy import LazyLoader, LazyStore
from marimo._save.stores.dict_store import DictStore
from marimo_export import Projection
from marimo_export._marimo import execution as execution_module
from marimo_export._marimo.execution import _run_targeted_cells
from marimo_export._marimo.runner import (
    _raise_failures,
    _scenario_bindings,
)
from marimo_export.plan import decode_plan


def test_child_execution_cannot_mutate_recorded_scenario_inputs() -> None:
    plan = decode_plan(
        {
            "schema": "marimo-export.plan.v1",
            "inputs": {
                "settings": {
                    "definition": "settings",
                    "default": {"symbols": ["MSFT"]},
                }
            },
            "outputs": {
                "summary": {
                    "source": "summary",
                    "formats": {"json": {}},
                }
            },
        }
    )
    scenario = plan.scenarios[0]

    definitions, _ = _scenario_bindings(plan, scenario)
    settings = definitions["settings"]
    assert isinstance(settings, dict)
    symbols = settings["symbols"]
    assert isinstance(symbols, list)
    symbols.append("CRWV")

    assert scenario.inputs == {"settings": {"symbols": ["MSFT"]}}


def test_cell_execution_flushes_active_marimo_caches_before_and_after(monkeypatch) -> None:
    events: list[object] = []

    class Runner:
        _kernel = SimpleNamespace(argv=["notebook.py", "--flag"])

        async def run(self, cells: set[object]) -> None:
            events.append(("run", cells))

    monkeypatch.setattr(execution_module, "flush_caches", lambda: events.append("flush"))
    monkeypatch.setattr(sys, "argv", ["root.py"])

    asyncio.run(_run_targeted_cells(cast(Any, Runner()), cast(Any, {"cell"})))

    assert events == ["flush", ("run", {"cell"}), "flush"]
    assert sys.argv == ["notebook.py", "--flag"]


def test_captured_execution_failure_is_raised_without_scenario_wrapping() -> None:
    cause = ValueError("projection failed")

    with pytest.raises(ValueError) as raised:
        _raise_failures([cause])

    assert raised.value is cause
    assert str(raised.value) == "projection failed"


def test_marimo_lazy_cache_restores_the_complete_projection() -> None:
    projection = Projection(
        b"portable bytes",
        format_id="custom.v1",
        metadata={"source": "test"},
    )
    loader = LazyLoader(
        name="projection_roundtrip",
        store=LazyStore(DictStore()),
        signer=None,
        mode="off",
    )
    cached = Cache(
        defs={},
        hash="projection",
        cache_type="Pure",
        stateful_refs=set(),
        hit=False,
        meta={"return": projection},
    )

    assert loader.save_cache(cached) is True
    loader.flush()
    restored = loader.load_cache(HashKey(hash="projection", cache_type="Pure"))

    assert restored is not None
    assert restored.meta["return"] == projection
    scope: dict[str, object] = {}
    restored.restore(scope)
    assert scope == {}
    assert Projection.__module__ == "marimo_export"


def test_polars_cache_restore_uses_native_ipc_in_a_worker_process() -> None:
    script = """
from marimo._save.cache import Cache
from marimo._save.hash import HashKey
from marimo._save.loaders.lazy import LazyLoader, LazyStore
from marimo._save.stores.dict_store import DictStore
from marimo._save.stubs.lazy_stub import BLOB_DESERIALIZERS
from marimo_export._marimo.cache import polars_cache_restore_scope
from marimo_export.projection.exporters import arrow, parquet
import polars as pl

frame = pl.DataFrame({"value": [1, 2]})
series = pl.Series("value", [1, 2])
loader = LazyLoader(
    name="polars_roundtrip",
    store=LazyStore(DictStore()),
    signer=None,
    mode="off",
)
cached = Cache(
    defs={"frame": frame, "series": series},
    hash="polars",
    cache_type="Pure",
    stateful_refs=set(),
    hit=False,
    meta={},
)
assert loader.save_cache(cached) is True
loader.flush()
upstream = BLOB_DESERIALIZERS[".arrow"]
with polars_cache_restore_scope():
    restored = loader.load_cache(HashKey(hash="polars", cache_type="Pure"))
    assert restored is not None
    scope = {}
    restored.restore(scope)
assert BLOB_DESERIALIZERS[".arrow"] is upstream
assert scope["frame"].equals(frame)
assert scope["series"].equals(series)
assert arrow(scope["frame"]).payload
assert parquet(scope["frame"]).payload.startswith(b"PAR1")
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
