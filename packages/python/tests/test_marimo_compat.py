from __future__ import annotations

import hashlib
import importlib
import subprocess
import sys
import weakref
from contextlib import nullcontext
from importlib.machinery import ModuleSpec, SourceFileLoader
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import marimo._runtime.executor.lifecycles.cached as cached_lifecycle
import marimo._save.loaders as native_loaders
import marimo_export._marimo.compat as marimo_compat
import msgspec
import pytest
from marimo._save.stubs.lazy_stub import Cache, CacheType, Item, Meta
from marimo_export._execution import MatrixPlan, OutputProjection
from marimo_export._marimo.compat import (
    _cleanup_state_child,
    _document_sha256,
    _include_package_parents,
    _isolated_modules,
    _native_receipt,
    _ReadSnapshotStore,
    _release_state_child,
    _track_upstream_cache,
    flush_native_caches,
    preflight_exporters,
    prepared_exporters,
    require_capabilities,
)
from marimo_export.errors import OutputError
from marimo_export.exporters import anywidget, importable


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


@pytest.mark.parametrize("fail", [False, True], ids=["success", "error"])
def test_exporter_module_overlay_restores_new_package_bindings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail: bool,
) -> None:
    package_name = f"marimo_export_test_overlay_{'error' if fail else 'success'}"
    module_name = f"{package_name}.exporter"
    package = tmp_path / package_name
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "exporter.py").write_text("value = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    original_modules = dict(sys.modules)
    names: set[str] = {module_name}
    _include_package_parents(names)

    with (
        pytest.raises(RuntimeError) if fail else nullcontext(),
        _isolated_modules(names, original_modules, roots={module_name}),
    ):
        imported = importlib.import_module(module_name)
        imported_package = sys.modules[package_name]
        assert imported_package.exporter is imported
        if fail:
            raise RuntimeError("stop")

    assert package_name not in sys.modules
    assert module_name not in sys.modules


def test_exporter_module_overlay_restores_existing_package_identities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_name = "marimo_export_test_overlay_existing"
    module_name = f"{package_name}.exporter"
    package = tmp_path / package_name
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "exporter.py").write_text("value = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    original_module = importlib.import_module(module_name)
    original_package = sys.modules[package_name]
    original_modules = dict(sys.modules)
    names: set[str] = {module_name}
    _include_package_parents(names)

    with _isolated_modules(names, original_modules, roots={module_name}):
        imported = importlib.import_module(module_name)
        imported_package = sys.modules[package_name]
        assert imported is not original_module
        assert imported_package is not original_package
        assert imported_package.exporter is imported

    assert sys.modules[package_name] is original_package
    assert sys.modules[module_name] is original_module
    assert original_package.exporter is original_module


def test_exporter_module_overlay_shadows_source_parent_of_native_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_name = "marimo_export_test_native_parent"
    package = tmp_path / package_name
    package.mkdir()
    source = package / "__init__.py"
    source.write_text(
        "import sys\nfrom .child import encode\nself_ref = sys.modules[__name__]\n",
        encoding="utf-8",
    )
    child_name = f"{package_name}.child"
    child_package = package / "child"
    child_package.mkdir()
    child_source = child_package / "__init__.py"
    child_source.write_text(
        "def encode():\n    return 'first'\n",
        encoding="utf-8",
    )
    native_name = f"{child_name}._native"
    monkeypatch.syspath_prepend(str(tmp_path))
    original = importlib.import_module(package_name)
    original_child = sys.modules[child_name]
    native_file = tmp_path / "_native.so"
    native_file.write_bytes(b"native")
    native = ModuleType(native_name)
    native.__file__ = str(native_file)
    native.__spec__ = ModuleSpec(native_name, loader=None, origin=str(native_file))
    monkeypatch.setitem(sys.modules, native_name, native)
    monkeypatch.setattr(original_child, "_native", native, raising=False)
    child_source.write_text(
        "def encode():\n    return 'other'\n",
        encoding="utf-8",
    )
    original_modules = dict(sys.modules)
    names: set[str] = {package_name, child_name, native_name}

    with _isolated_modules(names, original_modules, roots={package_name}):
        fresh = importlib.import_module(package_name)
        fresh_child = sys.modules[child_name]
        assert fresh is not original
        assert fresh.self_ref is fresh
        assert fresh.encode() == "other"
        assert fresh.encode is fresh_child.encode
        assert fresh.child is fresh_child
        assert sys.modules[native_name] is native
        assert fresh_child._native is native

    assert sys.modules[package_name] is original
    assert sys.modules[child_name] is original_child
    assert sys.modules[native_name] is native


@pytest.mark.parametrize("preexisting", [False, True], ids=["new", "existing"])
@pytest.mark.parametrize("fail", [False, True], ids=["success", "base-exception"])
def test_exporter_module_overlay_restores_namespace_packages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preexisting: bool,
    fail: bool,
) -> None:
    package_name = (
        f"marimo_export_test_namespace_{'existing' if preexisting else 'new'}_"
        f"{'error' if fail else 'success'}"
    )
    module_name = f"{package_name}.exporter"
    package = tmp_path / package_name
    package.mkdir()
    (package / "exporter.py").write_text("value = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    original_package = importlib.import_module(package_name) if preexisting else None
    original_modules = dict(sys.modules)
    names: set[str] = {module_name}
    _include_package_parents(names)

    with (
        pytest.raises(KeyboardInterrupt) if fail else nullcontext(),
        _isolated_modules(names, original_modules, roots={module_name}),
    ):
        imported = importlib.import_module(module_name)
        assert sys.modules[package_name].exporter is imported
        if fail:
            raise KeyboardInterrupt

    assert module_name not in sys.modules
    if original_package is None:
        assert package_name not in sys.modules
    else:
        assert sys.modules[package_name] is original_package
        assert not hasattr(original_package, "exporter")


def test_exporter_module_overlay_restores_setup_after_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_name = "marimo_export_test_overlay_cancelled"
    module_name = f"{package_name}.exporter"
    package = tmp_path / package_name
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "exporter.py").write_text("value = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    original_module = importlib.import_module(module_name)
    original_package = sys.modules[package_name]
    original_get_code = SourceFileLoader.get_code
    original_modules = dict(sys.modules)
    names = {module_name}
    native_invalidate = importlib.invalidate_caches
    calls = 0

    def cancel_once() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise KeyboardInterrupt
        native_invalidate()

    monkeypatch.setattr(marimo_compat.importlib, "invalidate_caches", cancel_once)

    with (
        pytest.raises(KeyboardInterrupt),
        _isolated_modules(
            names,
            original_modules,
            roots={module_name},
        ),
    ):
        pass

    assert sys.modules[package_name] is original_package
    assert sys.modules[module_name] is original_module
    assert original_package.exporter is original_module
    assert SourceFileLoader.get_code is original_get_code


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


def test_state_child_release_runs_the_registered_marimo_finalizer() -> None:
    class Runner:
        pass

    class Parent:
        def __init__(self, child_context: object) -> None:
            self.children = [child_context]
            self.released: list[object] = []

        def remove_child(self, child_context: object) -> None:
            self.children.remove(child_context)
            self.released.append(child_context)

    runner = Runner()
    child_context = object()
    parent = Parent(child_context)
    finalizer = weakref.finalize(runner, parent.remove_child, child_context)
    finalizer.atexit = False

    _release_state_child(
        child=runner,
        parent_context=parent,
        child_context=child_context,
    )

    assert parent.children == []
    assert parent.released == [child_context]
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


def _custom_exporter_plan(module_name: str, symbol: str = "encode") -> MatrixPlan:
    projection = OutputProjection(
        name="summary",
        source="value",
        exporter=importable(f"{module_name}:{symbol}"),
    )
    return MatrixPlan(
        states=(),
        inputs=(),
        outputs=("summary",),
        projections={"summary": projection},
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


def test_prepared_exporters_retains_new_native_package_graph() -> None:
    code = """
import sys
from marimo_export._execution import MatrixPlan, OutputProjection
from marimo_export._marimo.compat import prepared_exporters
from marimo_export.exporters import importable

assert "numpy" not in sys.modules
projection = OutputProjection(
    name="summary",
    source="value",
    exporter=importable("numpy:array"),
)
plan = MatrixPlan(
    states=(),
    inputs=(),
    outputs=("summary",),
    projections={"summary": projection},
    ordinary_cells={},
    state_name="marimo_export_state_0123456789abcdef",
    state_code="marimo_export_state_0123456789abcdef = 'state'",
)
original = None
for index in range(2):
    with prepared_exporters(plan):
        current = sys.modules["numpy"]
        if index == 0:
            original = current
        else:
            assert current is not original
    assert sys.modules["numpy"] is original
    assert "numpy._core._multiarray_umath" in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_prepared_exporters_restores_source_parent_of_native_callable() -> None:
    module_name = "msgspec"
    original = importlib.import_module(module_name)
    plan = _custom_exporter_plan(module_name, "to_builtins")

    with prepared_exporters(plan):
        assert sys.modules[module_name] is not original

    assert sys.modules[module_name] is original

    with prepared_exporters(plan):
        assert sys.modules[module_name] is not original

    assert sys.modules[module_name] is original


def test_prepared_exporters_reloads_explicit_source_parent_of_native_root() -> None:
    module_name = "msgspec"
    native_name = "msgspec._core"
    original = importlib.import_module(module_name)
    native = importlib.import_module(native_name)
    projections = {
        "source": OutputProjection(
            name="source",
            source="value",
            exporter=importable(f"{module_name}:to_builtins"),
        ),
        "native": OutputProjection(
            name="native",
            source="value",
            exporter=importable(f"{native_name}:to_builtins"),
        ),
    }
    plan = MatrixPlan(
        states=(),
        inputs=(),
        outputs=("source", "native"),
        projections=projections,
        ordinary_cells={},
        state_name="marimo_export_state_0123456789abcdef",
        state_code="marimo_export_state_0123456789abcdef = 'state'",
    )

    with prepared_exporters(plan):
        assert sys.modules[module_name] is not original
        assert sys.modules[native_name] is native

    assert sys.modules[module_name] is original
    assert sys.modules[native_name] is native


def test_prepared_exporters_refreshes_loaded_source_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_name = "marimo_export_test_source_parent"
    exporter_name = f"{package_name}.exporter"
    native_name = f"{package_name}._native"
    package = tmp_path / package_name
    package.mkdir()
    source = package / "__init__.py"
    source.write_text("config = 'first'\n", encoding="utf-8")
    (package / "exporter.py").write_text(
        "from . import config\n\ndef encode(value):\n    return config, value\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    original_exporter = importlib.import_module(exporter_name)
    original_package = sys.modules[package_name]
    native_file = tmp_path / "source_parent_native.so"
    native_file.write_bytes(b"native")
    native = ModuleType(native_name)
    native.__file__ = str(native_file)
    native.__spec__ = ModuleSpec(native_name, loader=None, origin=str(native_file))
    monkeypatch.setitem(sys.modules, native_name, native)
    monkeypatch.setattr(original_package, "_native", native, raising=False)
    plan = _custom_exporter_plan(exporter_name)

    with prepared_exporters(plan) as first_identities:
        assert sys.modules[package_name] is not original_package
        assert sys.modules[exporter_name].encode(None) == ("first", None)

    source.write_text("config = 'other'\n", encoding="utf-8")
    with prepared_exporters(plan) as second_identities:
        fresh_package = sys.modules[package_name]
        fresh_exporter = sys.modules[exporter_name]
        assert fresh_package is not original_package
        assert fresh_package.config == "other"
        assert fresh_exporter.encode(None) == ("other", None)
        assert sys.modules[native_name] is native

    assert first_identities["summary"] != second_identities["summary"]
    assert sys.modules[package_name] is original_package
    assert sys.modules[exporter_name] is original_exporter
    assert sys.modules[native_name] is native


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


def test_exporter_preflight_fingerprints_sideloaded_function_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "marimo_export_test_sideload"
    plan = _custom_exporter_plan(module_name)

    first = ModuleType(module_name)
    exec("def encode(value):\n    return value + 1\n", first.__dict__)
    monkeypatch.setitem(sys.modules, module_name, first)
    first_identity = preflight_exporters(plan)["summary"]

    second = ModuleType(module_name)
    exec("def encode(value):\n    return value + 2\n", second.__dict__)
    monkeypatch.setitem(sys.modules, module_name, second)
    second_identity = preflight_exporters(plan)["summary"]

    assert len(first_identity) == 64
    assert first_identity != second_identity


def test_exporter_preflight_rejects_a_stale_loaded_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "marimo_export_test_stale_exporter"
    exporter = tmp_path / f"{module_name}.py"
    exporter.write_text("def encode(value):\n    return 'first', value\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()

    preflight_exporters(_custom_exporter_plan(module_name))
    exporter.write_text(
        "def encode(value):\n    return 'changed-and-longer', value\n",
        encoding="utf-8",
    )

    with pytest.raises(OutputError) as raised:
        preflight_exporters(_custom_exporter_plan(module_name))

    assert raised.value.code == "exporter_stale"


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

    monkeypatch.setattr(marimo_compat, "_exporter_dependencies", fail)

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


def test_exporter_preflight_accepts_package_owned_builtin_exporters() -> None:
    projection = OutputProjection(
        name="summary",
        source="value",
        exporter=anywidget.bundle(),
    )
    plan = MatrixPlan(
        states=(),
        inputs=(),
        outputs=("summary",),
        projections={"summary": projection},
        ordinary_cells={},
        state_name="marimo_export_state_0123456789abcdef",
        state_code="marimo_export_state_0123456789abcdef = 'state'",
    )

    identity = preflight_exporters(plan)["summary"]

    assert len(identity) == 64


def test_exporter_preflight_accepts_importable_callable_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "marimo_export_test_callable_instance"

    def identity(label: str) -> str:
        module = ModuleType(module_name)
        module.__dict__["label"] = label
        exec(
            "class Encoder:\n"
            "    def __init__(self, label):\n"
            "        self.label = label\n"
            "\n"
            "    def __call__(self, value):\n"
            "        return self.label, value\n"
            "\n"
            "    def __getattr__(self, name):\n"
            "        if name == '__code__':\n"
            "            return object()\n"
            "        raise AttributeError(name)\n"
            "\n"
            "encode = Encoder(label)\n",
            module.__dict__,
        )
        monkeypatch.setitem(sys.modules, module_name, module)
        return preflight_exporters(_custom_exporter_plan(module_name))["summary"]

    assert identity("first") != identity("second")


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


def test_exporter_preflight_checks_a_reexported_selected_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    implementation_name = "marimo_export_test_reexport_implementation"
    implementation = ModuleType(implementation_name)
    exec("def encode(value):\n    return value\n", implementation.__dict__)
    api_name = "marimo_export_test_reexport_api"
    api = ModuleType(api_name)
    vars(api)["encode"] = vars(implementation)["encode"]
    monkeypatch.setitem(sys.modules, implementation_name, implementation)
    monkeypatch.setitem(sys.modules, api_name, api)

    identity = preflight_exporters(_custom_exporter_plan(api_name))

    assert len(identity["summary"]) == 64


def test_exporter_preflight_checks_only_the_imported_local_member(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper_name = "marimo_export_test_imported_member_helper"
    exporter_name = "marimo_export_test_imported_member_exporter"
    (tmp_path / f"{helper_name}.py").write_text(
        "def transform(value):\n"
        "    return value\n"
        "\n"
        "def unrelated(value, seen=[]):\n"
        "    seen.append(value)\n"
        "    return len(seen)\n",
        encoding="utf-8",
    )
    (tmp_path / f"{exporter_name}.py").write_text(
        "def encode(value):\n"
        f"    from {helper_name} import transform\n"
        "\n"
        "    return transform(value)\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()

    identity = preflight_exporters(_custom_exporter_plan(exporter_name))

    assert len(identity["summary"]) == 64


def test_exporter_preflight_allows_local_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "marimo_export_test_scope_local_construction"
    (tmp_path / f"{module_name}.py").write_text(
        "def encode(value):\n"
        "    def normalize(result):\n"
        "        return result\n"
        "\n"
        "    result = {}\n"
        "    result['value'] = value\n"
        "    return normalize(result)\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()

    identity = preflight_exporters(_custom_exporter_plan(module_name))

    assert len(identity["summary"]) == 64


def test_exporter_preflight_checks_literal_getattr_on_a_local_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper_name = "marimo_export_test_getattr_helper"
    exporter_name = "marimo_export_test_getattr_exporter"
    helper_path = tmp_path / f"{helper_name}.py"
    helper_path.write_text(
        "def transform(value):\n    return 'first', value\n",
        encoding="utf-8",
    )
    (tmp_path / f"{exporter_name}.py").write_text(
        f"import {helper_name} as helper\n"
        "\n"
        "def encode(value):\n"
        '    return getattr(helper, "transform")(value)\n',
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()

    first = preflight_exporters(_custom_exporter_plan(exporter_name))["summary"]
    helper_path.write_text(
        "def transform(value):\n    return 'changed-and-longer', value\n",
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, exporter_name, raising=False)
    monkeypatch.delitem(sys.modules, helper_name, raising=False)
    importlib.invalidate_caches()
    second = preflight_exporters(_custom_exporter_plan(exporter_name))["summary"]

    assert first != second


def test_exporter_preflight_tracks_unversioned_transitive_module_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = (
        "marimo_export_test_multiroot_api",
        "marimo_export_test_multiroot_implementation",
        "marimo_export_test_multiroot_helper",
        "marimo_export_test_multiroot_deep",
    )
    roots = [tmp_path / f"root-{index}" for index in range(len(names))]
    for root in roots:
        root.mkdir()
        monkeypatch.syspath_prepend(str(root))
    api_name, implementation_name, helper_name, deep_name = names
    (roots[0] / f"{api_name}.py").write_text(
        f"from {implementation_name} import encode\n",
        encoding="utf-8",
    )
    (roots[1] / f"{implementation_name}.py").write_text(
        f"from {helper_name} import transform\n\ndef encode(value):\n    return transform(value)\n",
        encoding="utf-8",
    )
    (roots[2] / f"{helper_name}.py").write_text(
        f"from {deep_name} import PREFIX\n\ndef transform(value):\n    return PREFIX, value\n",
        encoding="utf-8",
    )
    deep_path = roots[3] / f"{deep_name}.py"
    deep_path.write_text("PREFIX = 'first'\n", encoding="utf-8")
    importlib.invalidate_caches()

    first = preflight_exporters(_custom_exporter_plan(api_name))["summary"]
    deep_path.write_text("PREFIX = 'changed-and-longer'\n", encoding="utf-8")
    for name in names:
        monkeypatch.delitem(sys.modules, name, raising=False)
    importlib.invalidate_caches()
    second = preflight_exporters(_custom_exporter_plan(api_name))["summary"]

    assert first != second


def test_exporter_preflight_tracks_globals_used_only_by_nested_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter_name = "marimo_export_test_nested_global_exporter"
    helper_name = "marimo_export_test_nested_global_helper"
    (tmp_path / f"{exporter_name}.py").write_text(
        f"import {helper_name} as helper\n"
        "\n"
        "def encode(value):\n"
        "    def nested():\n"
        "        return helper.transform(value)\n"
        "\n"
        "    return nested()\n",
        encoding="utf-8",
    )
    helper_path = tmp_path / f"{helper_name}.py"
    helper_path.write_text(
        "def transform(value):\n    return 'first', value\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()

    first = preflight_exporters(_custom_exporter_plan(exporter_name))["summary"]
    helper_path.write_text(
        "def transform(value):\n    return 'changed-and-longer', value\n",
        encoding="utf-8",
    )
    monkeypatch.delitem(sys.modules, exporter_name, raising=False)
    monkeypatch.delitem(sys.modules, helper_name, raising=False)
    importlib.invalidate_caches()
    second = preflight_exporters(_custom_exporter_plan(exporter_name))["summary"]

    assert first != second


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
