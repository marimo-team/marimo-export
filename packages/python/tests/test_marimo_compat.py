from __future__ import annotations

import asyncio
import hashlib
import importlib
import pickle
import sys
import weakref
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import marimo_export._marimo.compat.cache as cache_compat
import marimo_export._marimo.compat.exporters as exporter_compat
import msgspec
import pytest
from marimo._runtime.runner.hooks import NotebookCellHooks
from marimo._save.cache import Cache as RuntimeCache
from marimo._save.loaders.lazy import LazyLoader
from marimo._save.signing import CacheSignatureError, CacheSigner
from marimo._save.stores.dict_store import DictStore
from marimo._save.stubs.lazy_stub import Cache, CacheType, Item, Meta, UnhashableStub
from marimo_export._execution import ExportPlan, PlannedOutput
from marimo_export._marimo.compat.cache import (
    SequentialLazyLoader,
    _rerun_unavailable_attempt,
    sequential_cache_loader,
)
from marimo_export._marimo.compat.execution import (
    _cleanup_state_child,
    _native_receipt,
    _ReadSnapshotStore,
    _release_state_child,
)
from marimo_export._marimo.compat.exporters import preflight_exporters, prepared_exporters
from marimo_export._marimo.compat.inspection import _document_sha256
from marimo_export._marimo.compat.managed_kernel import kernel_lifespan
from marimo_export._marimo.composition import create_kernel_runtime
from marimo_export.errors import OutputError
from marimo_export.exporters import importable


def test_attached_marimo_exposes_live_capture_capabilities() -> None:
    report = create_kernel_runtime().require_capabilities()

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
        "synthetic_output_cells",
    )


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

    monkeypatch.setattr(cache_compat.asyncio, "sleep", observed_sleep)

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

    async with asyncio.timeout(1), sequential_cache_loader():
        assert PERSISTENT_LOADERS["lazy"] is not original

    assert PERSISTENT_LOADERS["lazy"] is original


def test_managed_cache_installation_is_idempotent() -> None:
    hooks = NotebookCellHooks()

    with cache_compat.managed_cache_compat(hooks):
        pass
    with cache_compat.managed_cache_compat(hooks):
        pass

    assert list(hooks.post_execution_hooks).count(cache_compat._flush_cache_writes) == 1


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
    cache_compat.add_cache_write_barrier(hooks)
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


def test_prepared_exporters_restores_loaded_modules_after_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_name = "marimo_export_test_cancelled_exporter"
    module_name = f"{package_name}.exporter"
    package = tmp_path / package_name
    package.mkdir()
    (package / "__init__.py").write_text("from .exporter import encode\n", encoding="utf-8")
    (package / "exporter.py").write_text(
        "def encode(value):\n    return value\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    original_module = importlib.import_module(module_name)
    original_package = sys.modules[package_name]

    with (
        pytest.raises(KeyboardInterrupt, match="cancelled"),
        prepared_exporters(_custom_exporter_plan(module_name)),
    ):
        assert sys.modules[package_name] is not original_package
        assert sys.modules[module_name] is not original_module
        raise KeyboardInterrupt("cancelled")

    assert sys.modules[package_name] is original_package
    assert sys.modules[module_name] is original_module
    assert original_package.exporter is original_module


def test_state_child_cleanup_releases_after_teardown_cancellation() -> None:
    events: list[str] = []

    class Runner:
        pass

    class Parent:
        def __init__(self, child_context: object) -> None:
            self.children = [child_context]

        def remove_child(self, child_context: object) -> None:
            self.children.remove(child_context)

    runner = Runner()
    child_context = object()
    parent = Parent(child_context)
    finalizer = weakref.finalize(runner, parent.remove_child, child_context)
    finalizer.atexit = False

    def teardown() -> None:
        events.append("teardown")
        raise KeyboardInterrupt("cancelled")

    def release() -> None:
        events.append("release")
        _release_state_child(
            child=runner,
            parent_context=parent,
            child_context=child_context,
        )

    with pytest.raises(KeyboardInterrupt, match="cancelled"):
        _cleanup_state_child(
            teardown=teardown,
            release=release,
            primary=None,
            state_name="baseline",
        )

    assert events == ["teardown", "release"]
    assert parent.children == []
    assert not finalizer.alive


def test_state_child_cleanup_preserves_the_execution_error() -> None:
    primary = ValueError("execution failed")

    def teardown() -> None:
        raise KeyboardInterrupt("cancelled")

    def release() -> None:
        raise RuntimeError("release failed")

    _cleanup_state_child(
        teardown=teardown,
        release=release,
        primary=primary,
        state_name="baseline",
    )

    assert primary.__notes__ == [
        "state child cleanup also failed: KeyboardInterrupt",
        "state child cleanup also failed: RuntimeError",
    ]


def _custom_exporter_plan(module_name: str, symbol: str = "encode") -> ExportPlan:
    planned_output = PlannedOutput(
        name="summary",
        source="value",
        exporter=importable(f"{module_name}:{symbol}"),
    )
    return ExportPlan(
        states=(),
        inputs=(),
        outputs=("summary",),
        planned_outputs={"summary": planned_output},
        ordinary_cells={},
        state_name="marimo_export_state_0123456789abcdef",
        state_code="marimo_export_state_0123456789abcdef = 'state'",
    )


def test_prepared_exporters_preserves_native_extension_modules() -> None:
    module_name = "numpy._core._multiarray_umath"
    plan = _custom_exporter_plan(module_name, "array")

    with prepared_exporters(plan):
        imported = importlib.import_module(module_name)

    assert sys.modules[module_name] is imported

    with prepared_exporters(plan):
        assert importlib.import_module(module_name) is imported

    assert sys.modules[module_name] is imported


def test_prepared_exporters_refreshes_loaded_source_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_name = "marimo_export_test_source_parent"
    exporter_name = f"{package_name}.exporter"
    bridge_name = f"{package_name}.bridge"
    config_name = f"{package_name}.config"
    package = tmp_path / package_name
    package.mkdir()
    (package / "__init__.py").write_text(
        "from .config import CONFIG as config\n",
        encoding="utf-8",
    )
    config_source = package / "config.py"
    config_source.write_text("CONFIG = 'first'\n", encoding="utf-8")
    (package / "bridge.py").write_text(
        "from .config import CONFIG\n",
        encoding="utf-8",
    )
    native_parent_package = package / "native_parent"
    native_parent_package.mkdir()
    native_parent_source = native_parent_package / "__init__.py"
    native_parent_source.write_text("", encoding="utf-8")
    (package / "exporter.py").write_text(
        "def encode(value):\n    from .bridge import CONFIG\n    return CONFIG, value\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    original_exporter = importlib.import_module(exporter_name)
    original_package = sys.modules[package_name]
    original_config = sys.modules[config_name]
    assert bridge_name not in sys.modules
    plan = _custom_exporter_plan(exporter_name)

    with prepared_exporters(plan) as first_identities:
        assert sys.modules[package_name] is not original_package
        assert sys.modules[exporter_name].encode(None) == ("first", None)
        assert sys.modules[bridge_name].CONFIG == "first"

    assert bridge_name not in sys.modules
    config_source.write_text("CONFIG = 'other'\n", encoding="utf-8")
    with prepared_exporters(plan) as second_identities:
        fresh_package = sys.modules[package_name]
        fresh_bridge = sys.modules[bridge_name]
        fresh_config = sys.modules[config_name]
        fresh_exporter = sys.modules[exporter_name]
        assert fresh_package is not original_package
        assert fresh_config is not original_config
        assert fresh_package.config == "other"
        assert fresh_bridge.CONFIG == "other"
        assert fresh_config.CONFIG == "other"
        assert fresh_exporter.encode(None) == ("other", None)

    assert first_identities["summary"] != second_identities["summary"]
    assert sys.modules[package_name] is original_package
    assert sys.modules[config_name] is original_config
    assert sys.modules[exporter_name] is original_exporter
    assert bridge_name not in sys.modules


def test_exporter_preflight_restores_modules_imported_before_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper_name = "marimo_export_test_failed_import_helper"
    exporter_name = "marimo_export_test_failed_import_exporter"
    (tmp_path / f"{helper_name}.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / f"{exporter_name}.py").write_text(
        f"import {helper_name}\nraise RuntimeError('failed import')\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()

    with (
        pytest.raises(OutputError) as raised,
        prepared_exporters(_custom_exporter_plan(exporter_name)),
    ):
        pass

    assert raised.value.code == "exporter_unavailable"
    assert helper_name not in sys.modules
    assert exporter_name not in sys.modules


def test_prepared_exporters_reports_shadow_initialization_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_name = "marimo_export_test_failed_shadow"
    native_name = f"{package_name}._native"
    package = tmp_path / package_name
    package.mkdir()
    source = package / "__init__.py"
    source.write_text(
        "def encode(value):\n    return value\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    original = importlib.import_module(package_name)
    native_file = tmp_path / "failed_shadow_native.so"
    native_file.write_bytes(b"native")
    native = ModuleType(native_name)
    native.__file__ = str(native_file)
    native.__spec__ = ModuleSpec(native_name, loader=None, origin=str(native_file))
    monkeypatch.setitem(sys.modules, native_name, native)
    monkeypatch.setattr(original, "_native", native, raising=False)
    source.write_text("raise RuntimeError('failed shadow')\n", encoding="utf-8")

    with (
        pytest.raises(OutputError) as raised,
        prepared_exporters(_custom_exporter_plan(package_name)),
    ):
        pass

    assert raised.value.code == "exporter_unavailable"
    assert raised.value.details == {
        "output": "summary",
        "exporter": f"{package_name}:encode",
        "exception_type": "RuntimeError",
    }
    assert sys.modules[package_name] is original
    assert sys.modules[native_name] is native


def test_exporter_identity_failures_are_output_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "marimo_export_test_identity_failure"
    module = ModuleType(module_name)
    exec("def encode(value):\n    return value\n", module.__dict__)
    monkeypatch.setitem(sys.modules, module_name, module)

    def fail(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise ValueError("dependency graph failed")

    monkeypatch.setattr(exporter_compat, "_exporter_dependencies", fail)

    with pytest.raises(OutputError) as raised:
        preflight_exporters(_custom_exporter_plan(module_name))

    assert raised.value.code == "exporter_identity_failed"
    assert raised.value.details == {
        "exception_type": "ValueError",
        "exporter": f"{module_name}:encode",
        "output": "summary",
    }


def test_native_receipt_uses_the_bytes_seen_by_the_snapshot() -> None:
    cache_key = "cell_cache/H_expected.jsonl"
    reference = "cell_cache/expected/return.npy"
    payload = b"verified payload"
    manifest = msgspec.json.encode(
        Cache(
            hash="expected",
            cache_type=CacheType.CONTENT_ADDRESSED,
            defs={},
            stateful_refs=[],
            meta=Meta(
                version=1,
                return_value=Item(reference=reference),
                blob_hashes={reference: hashlib.sha256(payload).hexdigest()},
            ),
        )
    )

    class MutatingStore:
        def __init__(self) -> None:
            self.calls: dict[str, int] = {}

        def get(self, key: str) -> bytes | None:
            call = self.calls.get(key, 0)
            self.calls[key] = call + 1
            if key == cache_key:
                return manifest if call == 0 else b'{"hash":"substituted"}'
            if key == reference:
                return payload if call == 0 else b"substituted payload"
            return None

    source = MutatingStore()
    snapshot = _ReadSnapshotStore(source)
    assert snapshot.get(cache_key) == manifest
    assert snapshot.get(reference) == payload

    receipt = _native_receipt(
        store=snapshot,
        cache_key=cache_key,
        expected_hash="expected",
        output="array",
        value=object(),
        disposition="hit",
    )

    assert receipt.payload == payload
    assert source.calls == {cache_key: 1, reference: 1}


def test_exporter_preflight_rejects_non_callable_symbols(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "marimo_export_test_non_callable"
    module = ModuleType(module_name)
    module.__dict__["encode"] = 42
    monkeypatch.setitem(sys.modules, module_name, module)

    with pytest.raises(OutputError) as raised:
        preflight_exporters(_custom_exporter_plan(module_name))

    assert raised.value.code == "exporter_invalid"
    assert str(raised.value) == (
        f"output 'summary' exporter '{module_name}:encode' is not callable"
    )


@pytest.mark.parametrize(
    ("first", "second"),
    [(1, 1.0), (0.0, -0.0)],
    ids=["integer-versus-float", "float-sign-zero"],
)
def test_exporter_preflight_preserves_python_scalar_identity(
    monkeypatch: pytest.MonkeyPatch,
    first: int | float,
    second: int | float,
) -> None:
    module_name = "marimo_export_test_scalar_identity"
    plan = _custom_exporter_plan(module_name)

    def identity(value: int | float) -> str:
        module = ModuleType(module_name)
        vars(module)["marker"] = value
        exec(
            "def encode(value, marker=marker):\n    return type(marker).__name__, marker, value\n",
            module.__dict__,
        )
        monkeypatch.setitem(sys.modules, module_name, module)
        return preflight_exporters(plan)["summary"]

    assert identity(first) != identity(second)
