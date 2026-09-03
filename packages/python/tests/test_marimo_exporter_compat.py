from __future__ import annotations

import asyncio
import importlib
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import cast

import marimo_export._marimo.compat.exporters as exporter_compat
import pytest
from marimo_export import OutputSpec
from marimo_export._execution import ExecutionPlan, NormalizedState, PlannedOutput
from marimo_export._execution.plan import exporter_token_name
from marimo_export._marimo.compat.exporters import (
    invoke_prepared_exporter,
    preflight_exporters,
    prepared_exporters,
)
from marimo_export._marimo.compat.inspection import _document_sha256
from marimo_export.errors import OutputError
from marimo_export.exporters import importable


def _custom_exporter_plan(
    module_name: str,
    symbol: str = "encode",
    *,
    dependencies: tuple[str, ...] = (),
) -> ExecutionPlan:
    exporter = importable(f"{module_name}:{symbol}", dependencies=dependencies)
    planned_output = PlannedOutput(
        name="summary",
        source=OutputSpec.export("value", exporter).source,
        exporter=exporter,
    )
    return ExecutionPlan(
        states=(
            NormalizedState(
                aliases=("baseline",),
                inputs={},
                fingerprint="c" * 64,
                ordinary_values={},
                ui_updates={},
            ),
        ),
        inputs=(),
        outputs=("summary",),
        planned_outputs={"summary": planned_output},
        ordinary_cells={},
        output_plan_sha256="a" * 64,
        spec_sha256="b" * 64,
        default_alias="baseline",
        default_fingerprint="c" * 64,
        baseline_fingerprint="b" * 64,
        document_sha256="d" * 64,
        state_name="marimo_export_state_0123456789abcdef",
        state_code="marimo_export_state_0123456789abcdef = 'state'",
    )


def test_document_digest_uses_portable_content_and_cell_identity() -> None:
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
            id="live-random-id",
            code="value = 1",
            name="_",
            config=config,
        )
    ]
    regenerated = [
        SimpleNamespace(
            id="fresh-random-id",
            code="value = 1",
            name="_",
            config=config,
        )
    ]
    named = [
        SimpleNamespace(
            id="live-random-id",
            code="value = 1",
            name="named_cell",
            config=config,
        )
    ]
    externally_scoped = [
        SimpleNamespace(
            id="5c0ee8ec-d28d-4cb7-a4dc-4a77a54326a7live-random-id",
            code="value = 1",
            name="_",
            config=config,
        )
    ]

    assert _document_sha256(live) == _document_sha256(reloaded)
    assert _document_sha256(live) == _document_sha256(externally_scoped)
    assert _document_sha256(reloaded) != _document_sha256(regenerated)
    assert _document_sha256(reloaded) != _document_sha256(named)


def test_prepared_exporters_keeps_normal_imports_stable_during_background_activity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_name = "marimo_export_test_stable_exporter"
    module_name = f"{package_name}.exporter"
    background_name = "marimo_export_test_background_import"
    package = tmp_path / package_name
    package.mkdir()
    (package / "__init__.py").write_text("from .exporter import encode\n", encoding="utf-8")
    (package / "exporter.py").write_text(
        "def encode(value):\n    return value\n",
        encoding="utf-8",
    )
    (tmp_path / f"{background_name}.py").write_text("value = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    imported: list[ModuleType] = []

    def import_in_background() -> None:
        imported.append(importlib.import_module(background_name))

    with prepared_exporters(_custom_exporter_plan(module_name)):
        original_module = sys.modules[module_name]
        original_package = sys.modules[package_name]
        worker = threading.Thread(target=import_in_background)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert sys.modules[package_name] is original_package
        assert sys.modules[module_name] is original_module

    with prepared_exporters(_custom_exporter_plan(module_name)):
        assert sys.modules[package_name] is original_package
        assert sys.modules[module_name] is original_module

    assert sys.modules[package_name] is original_package
    assert sys.modules[module_name] is original_module
    assert original_package.exporter is original_module
    assert imported == [sys.modules[background_name]]


def test_prepared_exporter_registry_clears_after_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "marimo_export_test_registry_cleanup"
    module = ModuleType(module_name)
    exec("def encode(value):\n    return value + 1\n", module.__dict__)
    monkeypatch.setitem(sys.modules, module_name, module)
    plan = _custom_exporter_plan(module_name)
    exporter = plan.planned_outputs["summary"].exporter
    assert exporter is not None
    token = exporter_token_name(exporter)

    with (
        pytest.raises(KeyboardInterrupt, match="cancelled"),
        prepared_exporters(plan),
    ):
        assert invoke_prepared_exporter(token, 2, {}) == 3
        raise KeyboardInterrupt("cancelled")

    with pytest.raises(OutputError, match="unavailable"):
        invoke_prepared_exporter(token, 2, {})


@pytest.mark.asyncio
async def test_prepared_exporter_registries_are_capture_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plans: list[ExecutionPlan] = []
    for suffix in ("first", "second"):
        module_name = f"marimo_export_test_registry_{suffix}"
        module = ModuleType(module_name)
        vars(module)["label"] = suffix
        exec("def encode(value):\n    return label, value\n", module.__dict__)
        monkeypatch.setitem(sys.modules, module_name, module)
        plans.append(_custom_exporter_plan(module_name))
    tokens: list[str] = []
    for plan in plans:
        exporter = plan.planned_outputs["summary"].exporter
        assert exporter is not None
        tokens.append(exporter_token_name(exporter))
    ready = asyncio.Event()
    entered = 0

    async def invoke(position: int) -> tuple[str, int]:
        nonlocal entered
        with prepared_exporters(plans[position]):
            entered += 1
            if entered == 2:
                ready.set()
            await ready.wait()
            with pytest.raises(OutputError, match="unavailable"):
                invoke_prepared_exporter(tokens[1 - position], position, {})
            return cast(
                tuple[str, int],
                invoke_prepared_exporter(tokens[position], position, {}),
            )

    assert await asyncio.gather(invoke(0), invoke(1)) == [
        ("first", 0),
        ("second", 1),
    ]


def test_prepared_exporters_preserves_preloaded_declared_modules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed_name = "marimo_export_test_installed_dependency"
    exporter_name = "marimo_export_test_installed_exporter"
    installed_root = tmp_path / "site-packages"
    installed_root.mkdir()
    (installed_root / f"{installed_name}.py").write_text(
        "marker = object()\n",
        encoding="utf-8",
    )
    (tmp_path / f"{exporter_name}.py").write_text(
        f"from {installed_name} import marker\n\ndef encode(value):\n    return marker, value\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(installed_root))
    monkeypatch.syspath_prepend(str(tmp_path))
    installed = importlib.import_module(installed_name)

    with prepared_exporters(_custom_exporter_plan(exporter_name, dependencies=(installed_name,))):
        assert sys.modules[installed_name] is installed

    assert sys.modules[installed_name] is installed


def test_prepared_exporters_requires_restart_after_declared_source_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_name = "marimo_export_test_source_parent"
    exporter_name = f"{package_name}.exporter"
    config_name = f"{package_name}.config"
    package = tmp_path / package_name
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    config_source = package / "config.py"
    config_source.write_text("CONFIG = 'first'\n", encoding="utf-8")
    (package / "exporter.py").write_text(
        "from .config import CONFIG\n\ndef encode(value):\n    return CONFIG, value\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    plan = _custom_exporter_plan(exporter_name, dependencies=(config_name,))

    with prepared_exporters(plan) as first_identities:
        original_exporter = sys.modules[exporter_name]
        original_package = sys.modules[package_name]
        original_config = sys.modules[config_name]
        assert sys.modules[package_name] is original_package
        assert sys.modules[exporter_name] is original_exporter
        assert sys.modules[config_name] is original_config

    config_source.write_text("CONFIG = 'other'\n", encoding="utf-8")
    with pytest.raises(OutputError) as raised, prepared_exporters(plan):
        pass

    assert raised.value.code == "exporter_source_changed"
    assert raised.value.details == {
        "output": "summary",
        "exporter": f"{exporter_name}:encode",
        "module": config_name,
    }
    assert len(first_identities["summary"].identity) == 64
    assert sys.modules[package_name] is original_package
    assert sys.modules[config_name] is original_config
    assert sys.modules[exporter_name] is original_exporter


def test_exporter_preflight_reports_normal_import_failure(
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
    assert helper_name in sys.modules
    assert exporter_name not in sys.modules


def test_prepared_exporters_uses_code_loaded_before_first_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "marimo_export_test_preloaded_exporter"
    source = tmp_path / f"{module_name}.py"
    source.write_text(
        "def encode(value):\n    return 'loaded-v1', value\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    loaded = importlib.import_module(module_name)
    source.write_text(
        "def encode(value):\n    return 'disk-v2', value\n",
        encoding="utf-8",
    )
    plan = _custom_exporter_plan(module_name)

    with prepared_exporters(plan) as first:
        assert loaded.encode(None) == ("loaded-v1", None)
    with prepared_exporters(plan) as second:
        assert sys.modules[module_name] is loaded

    assert first == second


def test_exporter_identity_failures_are_output_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "marimo_export_test_identity_failure"
    (tmp_path / f"{module_name}.py").write_text(
        "def encode(value):\n    return value\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    def fail(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise ValueError("dependency graph failed")

    monkeypatch.setattr(exporter_compat, "freeze_exporter_identity", fail)

    with pytest.raises(OutputError) as raised:
        preflight_exporters(_custom_exporter_plan(module_name))

    assert raised.value.code == "exporter_identity_failed"
    assert raised.value.details == {
        "exception_type": "ValueError",
        "exporter": f"{module_name}:encode",
        "output": "summary",
    }


def test_exporter_preflight_rejects_non_callable_symbols(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "marimo_export_test_non_callable"
    (tmp_path / f"{module_name}.py").write_text("encode = 42\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    with pytest.raises(OutputError) as raised:
        preflight_exporters(_custom_exporter_plan(module_name))

    assert raised.value.code == "exporter_invalid"
    assert str(raised.value) == (
        f"output 'summary' exporter '{module_name}:encode' is not callable"
    )


def test_exporter_preflight_keys_identity_by_declared_modules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "marimo_export_test_declared_identity"
    first_dependency = "marimo_export_test_declared_first"
    second_dependency = "marimo_export_test_declared_second"
    (tmp_path / f"{module_name}.py").write_text(
        "def encode(value):\n    return value\n",
        encoding="utf-8",
    )
    (tmp_path / f"{first_dependency}.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / f"{second_dependency}.py").write_text("value = 2\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    first = preflight_exporters(
        _custom_exporter_plan(module_name, dependencies=(first_dependency,))
    )["summary"]
    second = preflight_exporters(
        _custom_exporter_plan(module_name, dependencies=(second_dependency,))
    )["summary"]

    assert first != second
