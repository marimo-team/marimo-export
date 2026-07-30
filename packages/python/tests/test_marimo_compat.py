from __future__ import annotations

import hashlib
import importlib
import sys
import weakref
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType, SimpleNamespace

import marimo_export._marimo.compat as marimo_compat
import msgspec
import pytest
from marimo._save.stubs.lazy_stub import Cache, CacheType, Item, Meta
from marimo_export._execution import MatrixPlan, OutputProjection
from marimo_export._marimo.compat import (
    _cleanup_state_child,
    _document_sha256,
    _native_receipt,
    _ReadSnapshotStore,
    _release_state_child,
    preflight_exporters,
    prepared_exporters,
    require_capabilities,
)
from marimo_export.errors import OutputError
from marimo_export.exporters import importable


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
