from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import marimo._runtime.executor.lifecycles.cached as cached_lifecycle
import marimo._save.loaders as native_loaders
import pytest
from marimo_export._execution import MatrixPlan, OutputProjection
from marimo_export._marimo.compat import (
    _cleanup_state_child,
    _document_sha256,
    _track_upstream_cache,
    flush_native_caches,
    preflight_exporters,
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

    def teardown() -> None:
        events.append("teardown")
        raise KeyboardInterrupt("cancelled")

    def release() -> None:
        events.append("release")

    with pytest.raises(KeyboardInterrupt, match="cancelled"):
        _cleanup_state_child(
            teardown=teardown,
            release=release,
            primary=None,
            state_name="baseline",
        )

    assert events == ["teardown", "release"]


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


def _custom_exporter_plan(module_name: str) -> MatrixPlan:
    projection = OutputProjection(
        name="summary",
        source="value",
        exporter=importable(f"{module_name}:encode"),
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
