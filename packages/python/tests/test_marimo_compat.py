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


def test_exporter_preflight_rejects_callable_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "marimo_export_test_stateful_exporter"
    module = ModuleType(module_name)
    exec(
        "class Encoder:\n"
        "    def __call__(self, value):\n"
        "        return value\n"
        "\n"
        "encode = Encoder()\n",
        module.__dict__,
    )
    monkeypatch.setitem(sys.modules, module_name, module)

    with pytest.raises(OutputError) as raised:
        preflight_exporters(_custom_exporter_plan(module_name))

    assert raised.value.code == "exporter_invalid"
    assert str(raised.value) == (
        f"output 'summary' exporter '{module_name}:encode' is not a top-level function"
    )


def test_exporter_preflight_checks_a_reexported_selected_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    implementation_name = "marimo_export_test_reexport_implementation"
    implementation = ModuleType(implementation_name)
    exec(
        "def encode(value, seen=[]):\n    seen.append(value)\n    return len(seen)\n",
        implementation.__dict__,
    )
    api_name = "marimo_export_test_reexport_api"
    api = ModuleType(api_name)
    vars(api)["encode"] = vars(implementation)["encode"]
    monkeypatch.setitem(sys.modules, implementation_name, implementation)
    monkeypatch.setitem(sys.modules, api_name, api)

    with pytest.raises(OutputError) as raised:
        preflight_exporters(_custom_exporter_plan(api_name))

    assert raised.value.code == "exporter_invalid"
    assert str(raised.value) == (f"output 'summary' exporter '{api_name}:encode' is not stateless")


def test_exporter_preflight_rejects_mutable_local_module_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_name = "marimo_export_test_module_state"
    exporter_name = "marimo_export_test_module_exporter"
    (tmp_path / f"{state_name}.py").write_text("seen = []\n", encoding="utf-8")
    (tmp_path / f"{exporter_name}.py").write_text(
        f"import {state_name} as state\n"
        "\n"
        "def encode(value):\n"
        "    state.seen.append(value)\n"
        "    return len(state.seen)\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()

    with pytest.raises(OutputError) as raised:
        preflight_exporters(_custom_exporter_plan(exporter_name))

    assert raised.value.code == "exporter_invalid"
    assert str(raised.value) == (
        f"output 'summary' exporter '{exporter_name}:encode' is not stateless"
    )


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


@pytest.mark.parametrize(
    ("case", "source"),
    [
        (
            "mutable-default",
            "def encode(value, seen=[]):\n    seen.append(value)\n    return len(seen)\n",
        ),
        (
            "mutable-global",
            "seen = []\n\ndef encode(value):\n    seen.append(value)\n    return len(seen)\n",
        ),
        (
            "partial-dependency",
            "from functools import partial\n"
            "\n"
            "def helper(value, *, prefix):\n"
            "    return prefix, value\n"
            "\n"
            "bound = partial(helper, prefix='summary')\n"
            "\n"
            "def encode(value):\n"
            "    return bound(value)\n",
        ),
        (
            "wrapped-closure",
            "from functools import wraps\n"
            "\n"
            "def decorate(function):\n"
            "    @wraps(function)\n"
            "    def wrapper(value):\n"
            "        return function(value)\n"
            "    return wrapper\n"
            "\n"
            "@decorate\n"
            "def encode(value):\n"
            "    return value\n",
        ),
        (
            "bound-builtin",
            "seen = []\n"
            "append = seen.append\n"
            "\n"
            "def encode(value):\n"
            "    append(value)\n"
            "    return len(seen)\n",
        ),
        (
            "global-write",
            "count = 0\n"
            "\n"
            "def encode(value):\n"
            "    global count\n"
            "    count += 1\n"
            "    return value, count\n",
        ),
        (
            "class-state",
            "class State:\n"
            "    seen = []\n"
            "\n"
            "def encode(value):\n"
            "    State.seen.append(value)\n"
            "    return len(State.seen)\n",
        ),
    ],
)
def test_exporter_preflight_rejects_hidden_function_state(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    source: str,
) -> None:
    module_name = f"marimo_export_test_{case.replace('-', '_')}"
    module = ModuleType(module_name)
    exec(source, module.__dict__)
    monkeypatch.setitem(sys.modules, module_name, module)

    with pytest.raises(OutputError) as raised:
        preflight_exporters(_custom_exporter_plan(module_name))

    assert raised.value.code == "exporter_invalid"
    assert str(raised.value) == (
        f"output 'summary' exporter '{module_name}:encode' is not stateless"
    )
    assert isinstance(raised.value.details["dependency"], str)
    assert isinstance(raised.value.details["python_type"], str)
