"""Resolve exporters against the modules loaded by the kernel process."""

from __future__ import annotations

import importlib
import importlib.metadata
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import MappingProxyType, ModuleType
from typing import Any

from marimo_export._diagnostics import record_cleanup_failure
from marimo_export._execution.plan import Baseline, ExecutionPlan, exporter_token_name
from marimo_export._marimo.capabilities import PreparedExporter
from marimo_export._marimo.compat.exporter_identity import (
    ExporterKey,
    freeze_exporter_identity,
    verify_exporter_sources,
)
from marimo_export.errors import OutputError
from marimo_export.exporters._definitions import runtime_reference
from marimo_export.exporters._spec import ExporterSpec

_PREPARED_EXPORTERS: ContextVar[Mapping[str, Any] | None] = ContextVar(
    "marimo_export_prepared_exporters",
    default=None,
)


@dataclass(frozen=True, slots=True)
class _PreparedSelection:
    output: str
    key: ExporterKey
    modules: Mapping[str, ModuleType]


@dataclass(frozen=True, slots=True)
class _PreparedExporters:
    identities: Mapping[str, str]
    callables: Mapping[str, Any]
    selections: tuple[_PreparedSelection, ...]


def preflight_exporters(plan: ExecutionPlan) -> Mapping[str, str]:
    """Resolve selected exporters and return their cache identities."""

    return _prepare_exporters(plan, {}, {}).identities


@contextmanager
def prepared_exporters(
    plan: ExecutionPlan,
    baseline: Baseline | None = None,
) -> Iterator[Mapping[str, PreparedExporter]]:
    """Freeze exporter sources for one capture operation."""

    loaded_modules = {
        value.__name__: value
        for definition in (() if baseline is None else baseline.definitions.values())
        if isinstance((value := definition.value), ModuleType)
    }
    loaded_callables = {
        (str(getattr(value, "__module__", "")), str(getattr(value, "__name__", ""))): value
        for definition in (() if baseline is None else baseline.definitions.values())
        if callable(value := definition.value)
    }
    prepared = _prepare_exporters(plan, loaded_modules, loaded_callables)
    values: dict[str, PreparedExporter] = {}
    registry: dict[str, Any] = {}
    for output, identity in prepared.identities.items():
        exporter = plan.planned_outputs[output].exporter
        if exporter is None:
            raise RuntimeError("prepared exporter output has no exporter specification")
        custom = ":" in exporter.name
        values[output] = PreparedExporter(
            identity=identity,
            token=exporter_token_name(exporter) if custom else None,
        )
        if custom:
            token = exporter_token_name(exporter)
            callable_value = prepared.callables[output]
            previous = registry.setdefault(token, callable_value)
            if previous is not callable_value:
                raise OutputError(
                    "custom exporter token resolves to conflicting callables",
                    code="exporter_identity_failed",
                )
    registry_context = _PREPARED_EXPORTERS.set(MappingProxyType(registry))
    try:
        try:
            yield values
        except BaseException as primary:
            try:
                _verify_prepared(prepared)
            except BaseException as drift:
                record_cleanup_failure(primary, "exporter consistency", drift)
            raise
        else:
            _verify_prepared(prepared)
    finally:
        _PREPARED_EXPORTERS.reset(registry_context)


def invoke_prepared_exporter(
    token: str,
    value: Any,
    options: Mapping[str, Any],
) -> Any:
    """Call the capture-scoped exporter resolved during preflight."""

    registry = _PREPARED_EXPORTERS.get()
    exporter = registry.get(token) if registry is not None else None
    if not callable(exporter):
        raise OutputError(
            "the prepared custom exporter is unavailable in this capture",
            code="exporter_unavailable",
            details={"token": token},
        )
    return exporter(value, **dict(options))


def _prepare_exporters(
    plan: ExecutionPlan,
    loaded_modules: Mapping[str, ModuleType],
    loaded_callables: Mapping[tuple[str, str], Any],
) -> _PreparedExporters:
    selected = {
        output: planned_output.exporter
        for output, planned_output in plan.planned_outputs.items()
        if planned_output.exporter is not None
    }
    if not selected:
        return _PreparedExporters({}, {}, ())
    try:
        package_distributions = importlib.metadata.packages_distributions()
    except Exception:
        package_distributions = {}
    resolved: dict[ExporterKey, tuple[str, Mapping[str, ModuleType], Any]] = {}
    identities: dict[str, str] = {}
    callables: dict[str, Any] = {}
    selections: list[_PreparedSelection] = []
    for output, exporter in selected.items():
        key = (exporter.name, exporter.dependencies)
        previous = resolved.get(key)
        if previous is None:
            cache_identity, modules, value = _prepare_exporter(
                output=output,
                exporter=exporter,
                package_distributions=package_distributions,
                loaded_modules=loaded_modules,
                loaded_callables=loaded_callables,
            )
            resolved[key] = (cache_identity, modules, value)
        else:
            cache_identity, modules, value = previous
        identities[output] = cache_identity
        callables[output] = value
        selections.append(_PreparedSelection(output=output, key=key, modules=modules))
    return _PreparedExporters(identities, callables, tuple(selections))


def _prepare_exporter(
    *,
    output: str,
    exporter: ExporterSpec,
    package_distributions: Mapping[str, list[str]],
    loaded_modules: Mapping[str, ModuleType],
    loaded_callables: Mapping[tuple[str, str], Any],
) -> tuple[str, Mapping[str, ModuleType], Any]:
    reference = runtime_reference(exporter.name)
    declared = _declared_modules(exporter.name, exporter.dependencies)
    try:
        module = _loaded_module(
            reference.module,
            loaded_modules,
        ) or importlib.import_module(reference.module)
    except Exception as error:
        raise OutputError(
            f"output {output!r} exporter {exporter.name!r} is unavailable",
            code="exporter_unavailable",
            details={
                "output": output,
                "exporter": exporter.name,
                "exception_type": type(error).__name__,
            },
        ) from error
    resolved_dependencies: dict[str, ModuleType] = {}
    for dependency in exporter.dependencies:
        try:
            resolved_dependencies[dependency] = _loaded_module(
                dependency,
                loaded_modules,
            ) or importlib.import_module(dependency)
        except Exception as error:
            raise OutputError(
                f"output {output!r} exporter dependency {dependency!r} is unavailable",
                code="exporter_dependency_unavailable",
                details={
                    "output": output,
                    "exporter": exporter.name,
                    "dependency": dependency,
                    "exception_type": type(error).__name__,
                },
            ) from error
    value = loaded_callables.get(
        (reference.module, reference.symbol),
        getattr(module, reference.symbol, None),
    )
    if not callable(value):
        raise OutputError(
            f"output {output!r} exporter {exporter.name!r} is not callable",
            code="exporter_invalid",
            details={"output": output, "exporter": exporter.name},
        )
    modules = _loaded_declared_modules(declared)
    modules = {
        **modules,
        reference.module: module,
        **resolved_dependencies,
    }
    try:
        identity = freeze_exporter_identity(
            output=output,
            key=(exporter.name, exporter.dependencies),
            module=module,
            value=value,
            modules=modules,
            distributions=reference.distributions,
            package_distributions=package_distributions,
        )
    except OutputError:
        raise
    except Exception as error:
        raise OutputError(
            f"output {output!r} exporter {exporter.name!r} could not be fingerprinted",
            code="exporter_identity_failed",
            details={
                "output": output,
                "exporter": exporter.name,
                "exception_type": type(error).__name__,
            },
        ) from error
    return identity.cache, modules, value


def _verify_prepared(prepared: _PreparedExporters) -> None:
    seen: set[ExporterKey] = set()
    for selection in prepared.selections:
        if selection.key in seen:
            continue
        seen.add(selection.key)
        verify_exporter_sources(
            output=selection.output,
            key=selection.key,
            modules=selection.modules,
        )


def _declared_modules(name: str, dependencies: tuple[str, ...]) -> frozenset[str]:
    reference = runtime_reference(name)
    names = {reference.module, *dependencies}
    for module_name in tuple(names):
        parent = module_name.rpartition(".")[0]
        while parent:
            names.add(parent)
            parent = parent.rpartition(".")[0]
    return frozenset(names)


def _loaded_declared_modules(names: frozenset[str]) -> Mapping[str, ModuleType]:
    modules: dict[str, ModuleType] = {}
    for name in sorted(names):
        module = sys.modules.get(name)
        if isinstance(module, ModuleType):
            modules[name] = module
    return modules


def _loaded_module(
    name: str,
    loaded_modules: Mapping[str, ModuleType],
) -> ModuleType | None:
    if name in loaded_modules:
        return loaded_modules[name]
    try:
        from marimo._runtime.context import get_context

        globals_ = get_context().globals
    except Exception:
        globals_ = {}
    for value in globals_.values():
        if isinstance(value, ModuleType) and value.__name__ == name:
            return value
    loaded = sys.modules.get(name)
    return loaded if isinstance(loaded, ModuleType) else None


__all__ = [
    "invoke_prepared_exporter",
    "preflight_exporters",
    "prepared_exporters",
]
