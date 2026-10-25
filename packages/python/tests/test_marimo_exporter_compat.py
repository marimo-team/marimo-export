from __future__ import annotations

import asyncio
import gc
import hashlib
import importlib
import sys
import threading
import weakref
from collections.abc import Mapping
from pathlib import Path
from types import CodeType, ModuleType, SimpleNamespace
from typing import TypeVar, cast

import pytest
from marimo_export import OutputSpec
from marimo_export._execution import ExecutionPlan, NormalizedState, PlannedOutput
from marimo_export._execution.plan import exporter_token_name
from marimo_export._marimo.compat.exporters import kernel_state
from marimo_export._marimo.compat.inspection import _notebook_cell
from marimo_export._marimo.compat.kernel import PrivateKernelRuntime
from marimo_export._notebook import cells_document_sha256
from marimo_export.errors import OutputError
from marimo_export.exporters import importable
from marimo_export.exporters._prepare import invoke_prepared_exporter, prepared_exporters

_RUNTIME = PrivateKernelRuntime()
_T = TypeVar("_T")


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


def _document_sha256(cells: list[SimpleNamespace]) -> str:
    return cells_document_sha256(_notebook_cell(cell) for cell in cells)


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

    with prepared_exporters(_RUNTIME, _custom_exporter_plan(module_name)):
        original_module = sys.modules[module_name]
        original_package = sys.modules[package_name]
        worker = threading.Thread(target=import_in_background)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert sys.modules[package_name] is original_package
        assert sys.modules[module_name] is original_module

    with prepared_exporters(_RUNTIME, _custom_exporter_plan(module_name)):
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
        prepared_exporters(_RUNTIME, plan),
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
        with prepared_exporters(_RUNTIME, plans[position]):
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

    with prepared_exporters(
        _RUNTIME, _custom_exporter_plan(exporter_name, dependencies=(installed_name,))
    ):
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

    with prepared_exporters(_RUNTIME, plan) as first_identities:
        original_exporter = sys.modules[exporter_name]
        original_package = sys.modules[package_name]
        original_config = sys.modules[config_name]
        assert sys.modules[package_name] is original_package
        assert sys.modules[exporter_name] is original_exporter
        assert sys.modules[config_name] is original_config

    config_source.write_text("CONFIG = 'other'\n", encoding="utf-8")
    with pytest.raises(OutputError) as raised, prepared_exporters(_RUNTIME, plan):
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


def test_prepared_exporters_reports_normal_import_failure(
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
        prepared_exporters(_RUNTIME, _custom_exporter_plan(exporter_name)),
    ):
        pass

    assert raised.value.code == "exporter_unavailable"
    assert helper_name in sys.modules
    assert exporter_name not in sys.modules


def test_prepared_exporters_uses_code_loaded_before_first_preparation(
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

    with prepared_exporters(_RUNTIME, plan) as first:
        assert loaded.encode(None) == ("loaded-v1", None)
    with prepared_exporters(_RUNTIME, plan) as second:
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

    class FailingDigestRuntime(PrivateKernelRuntime):
        @staticmethod
        def code_sha256(code: CodeType) -> str:
            del code
            raise ValueError("code digest failed")

    with (
        pytest.raises(OutputError) as raised,
        prepared_exporters(FailingDigestRuntime(), _custom_exporter_plan(module_name)),
    ):
        pass

    assert raised.value.code == "exporter_identity_failed"
    assert raised.value.details == {
        "exception_type": "ValueError",
        "exporter": f"{module_name}:encode",
        "output": "summary",
    }


def test_prepared_exporters_rejects_non_callable_symbols(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "marimo_export_test_non_callable"
    (tmp_path / f"{module_name}.py").write_text("encode = 42\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    with (
        pytest.raises(OutputError) as raised,
        prepared_exporters(_RUNTIME, _custom_exporter_plan(module_name)),
    ):
        pass

    assert raised.value.code == "exporter_invalid"
    assert str(raised.value) == (
        f"output 'summary' exporter '{module_name}:encode' is not callable"
    )


def test_prepared_exporters_keys_identity_by_declared_modules(
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

    with (
        prepared_exporters(
            _RUNTIME, _custom_exporter_plan(module_name, dependencies=(first_dependency,))
        ) as first,
        prepared_exporters(
            _RUNTIME, _custom_exporter_plan(module_name, dependencies=(second_dependency,))
        ) as second,
    ):
        assert first["summary"].identity != second["summary"].identity


# The callable digest covers CPython bytecode, which changes between minor versions.
_PINNED_EXPORTER_IDENTITIES = {
    (3, 10): "3cc5ef17f292120e88968990127b02798e93ade32bcd9a2753be56cecff3e2c4",
    (3, 11): "5a568436b05764c4f9f8639744ef35541a65f1d4f8e2ef26621fc33f4ba1cef5",
    (3, 12): "5a568436b05764c4f9f8639744ef35541a65f1d4f8e2ef26621fc33f4ba1cef5",
    (3, 13): "a15da161edbb04093d6d113f0e5c36954a7fbcbc940c452ad59793815e090a5f",
    (3, 14): "08a2deb60425103fe6208dde16121ec29a29db502d4428d26a7db5e2ad3688bf",
}


def test_exporter_identity_is_pinned_for_a_fixture_exporter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper_name = "marimo_export_test_pinned_helper"
    exporter_name = "marimo_export_test_pinned_exporter"
    (tmp_path / f"{helper_name}.py").write_bytes(b"SCALE = 2\n")
    (tmp_path / f"{exporter_name}.py").write_bytes(
        f"from {helper_name} import SCALE\n\n"
        "def encode(value, offset=1, *, scale=SCALE):\n"
        "    return {'value': value + offset, 'scale': scale}\n\n"
        "encode.label = 'pinned'\n".encode()
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    with prepared_exporters(
        _RUNTIME, _custom_exporter_plan(exporter_name, dependencies=(helper_name,))
    ) as prepared:
        identity = prepared["summary"].identity

    expected = _PINNED_EXPORTER_IDENTITIES.get(sys.version_info[:2])
    if expected is None:
        pytest.fail(f"pin the fixture exporter identity for Python {sys.version_info[:2]}")
    assert identity == expected


class _KernelRuntime:
    def __init__(self, namespace: Mapping[str, object]) -> None:
        self.namespace = namespace
        self.states: dict[type, object] = {}

    def kernel_globals(self) -> Mapping[str, object]:
        return self.namespace

    def kernel_state(self, state_type: type[_T]) -> _T:
        state = self.states.setdefault(state_type, state_type())
        assert isinstance(state, state_type)
        return state

    def code_sha256(self, code: CodeType) -> str:
        return hashlib.sha256(code.co_code).hexdigest()


def test_prepared_exporters_resolve_modules_from_the_kernel_namespace() -> None:
    module_name = "marimo_export_test_kernel_namespace_exporter"
    module = ModuleType(module_name)
    exec("def encode(value):\n    return value * 2\n", module.__dict__)
    assert module_name not in sys.modules
    plan = _custom_exporter_plan(module_name)
    exporter = plan.planned_outputs["summary"].exporter
    assert exporter is not None

    with prepared_exporters(_KernelRuntime({"alias": module}), plan) as prepared:
        assert invoke_prepared_exporter(exporter_token_name(exporter), 21, {}) == 42

    assert len(prepared["summary"].identity) == 64
    assert module_name not in sys.modules


def test_kernel_state_is_scoped_to_one_kernel(monkeypatch: pytest.MonkeyPatch) -> None:
    import marimo._runtime.context as runtime_context

    class Registry:
        pass

    class State:
        pass

    kernel_registry = Registry()
    context: object = SimpleNamespace(app_kernel_runner_registry=kernel_registry)
    monkeypatch.setattr(runtime_context, "safe_get_context", lambda: context)
    state = kernel_state(State)

    # marimo gives an embedded app context its parent kernel's registry.
    context = SimpleNamespace(app_kernel_runner_registry=kernel_registry)
    assert kernel_state(State) is state

    context = SimpleNamespace(app_kernel_runner_registry=Registry())
    assert kernel_state(State) is not state

    context = None
    assert kernel_state(State) is kernel_state(State)
    assert kernel_state(State) is not state

    released = weakref.ref(state)
    del state, kernel_registry
    gc.collect()
    assert released() is None
