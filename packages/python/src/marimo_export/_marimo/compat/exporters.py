"""Resolve exporters and fingerprint their complete local source graph."""

from __future__ import annotations

import ast
import dis
import hashlib
import importlib
import importlib.metadata
import importlib.util
import inspect
import re
import struct
import sys
import threading
import weakref
from _thread import LockType
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from importlib.machinery import PathFinder, SourceFileLoader
from pathlib import Path
from typing import Any, cast

from marimo_export._execution.plan import ExportPlan
from marimo_export._json import JsonObject, JsonValue, canonical_bytes
from marimo_export.errors import OutputError
from marimo_export.exporters._definitions import runtime_reference

_MAX_EXPORTER_DEPENDENCIES = 256


def _exporter_value_identity(value: Any, label: str) -> JsonValue:
    active: set[int] = set()

    def encode(item: Any, item_label: str) -> JsonValue:
        if item is None:
            return {"type": "none"}
        if type(item) is bool:
            return {"type": "bool", "value": item}
        if type(item) is int:
            return {"type": "int", "value": str(item)}
        if type(item) is float:
            return {"type": "float64", "hex": struct.pack(">d", item).hex()}
        if type(item) is str:
            return {"type": "str", "value": item}
        if type(item) is bytes:
            return {"type": "bytes", "hex": item.hex()}
        if type(item) is object:
            return {"type": "sentinel"}
        if isinstance(item, re.Pattern):
            return {
                "type": "regex",
                "pattern": encode(item.pattern, f"{item_label} pattern"),
                "flags": item.flags,
            }
        if type(item) in {tuple, list, set, frozenset}:
            identifier = id(item)
            if identifier in active:
                raise TypeError(f"{item_label} contains a cycle")
            active.add(identifier)
            try:
                items = [
                    encode(member, f"{item_label}[{index}]") for index, member in enumerate(item)
                ]
            finally:
                active.remove(identifier)
            if type(item) in {set, frozenset}:
                items.sort(key=canonical_bytes)
            return {"type": type(item).__name__, "items": items}
        if type(item) is dict:
            identifier = id(item)
            if identifier in active:
                raise TypeError(f"{item_label} contains a cycle")
            active.add(identifier)
            try:
                entries = [
                    {
                        "key": encode(key, f"{item_label} key"),
                        "value": encode(member, f"{item_label}[{key!r}]"),
                    }
                    for key, member in item.items()
                ]
            finally:
                active.remove(identifier)
            entries.sort(key=canonical_bytes)
            return {"type": "dict", "entries": entries}
        raise TypeError(
            f"{item_label} has unsupported type {type(item).__module__}.{type(item).__qualname__}"
        )

    return encode(value, label)


@dataclass(frozen=True, slots=True)
class _ExporterIdentity:
    cache: str
    runtime: str
    environment: str
    modules: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ExporterSnapshot:
    module: weakref.ReferenceType[Any]
    runtime: str
    environment: str
    modules: tuple[str, ...]


_EXPORTER_SNAPSHOT_LOCK = threading.Lock()
_EXPORTER_SNAPSHOTS: dict[str, _ExporterSnapshot] = {}
_EXPORTER_SNAPSHOT_STATE_ATTRIBUTE = "_marimo_export_exporter_snapshot_state"
_EXPORTER_IMPORT_LOCK = threading.RLock()


def preflight_exporters(
    plan: ExportPlan,
    *,
    source_modules: frozenset[str] = frozenset(),
) -> Mapping[str, str]:
    """Resolve selected exporters and return their cache identities."""

    selected = {
        output: planned_output.exporter
        for output, planned_output in plan.planned_outputs.items()
        if planned_output.exporter is not None
    }
    if not selected:
        return {}
    identities: dict[str, str] = {}
    resolved: dict[str, str] = {}
    try:
        package_distributions = importlib.metadata.packages_distributions()
    except Exception:
        package_distributions = {}
    for output, exporter in selected.items():
        identity = resolved.get(exporter.name)
        if identity is not None:
            identities[output] = identity
            continue
        reference = runtime_reference(exporter.name)
        try:
            parent = reference.module.rpartition(".")[0]
            if parent in source_modules:
                importlib.import_module(parent)
            module = importlib.import_module(reference.module)
            value = getattr(module, reference.symbol)
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
        if not callable(value):
            raise OutputError(
                f"output {output!r} exporter {exporter.name!r} is not callable",
                code="exporter_invalid",
                details={
                    "output": output,
                    "exporter": exporter.name,
                },
            )
        try:
            identity = _exporter_identity(
                name=exporter.name,
                module=module,
                value=value,
                distributions=reference.distributions,
                package_distributions=package_distributions,
            )
            _record_exporter_snapshot(
                output=output,
                name=exporter.name,
                module=module,
                identity=identity,
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
        resolved[exporter.name] = identity.cache
        identities[output] = identity.cache
    return identities


@contextmanager
def prepared_exporters(plan: ExportPlan) -> Iterator[Mapping[str, str]]:
    """Resolve custom exporters in a capture-scoped module overlay."""

    custom = {
        planned_output.exporter.name: runtime_reference(planned_output.exporter.name).module
        for planned_output in plan.planned_outputs.values()
        if planned_output.exporter is not None and ":" in planned_output.exporter.name
    }
    if not custom:
        yield preflight_exporters(plan)
        return

    with _EXPORTER_IMPORT_LOCK:
        original_modules = dict(sys.modules)
        candidates = set(custom.values())
        candidates.update(_recorded_exporter_modules(custom))
        _include_loaded_source_dependencies(
            candidates,
            original_modules,
            roots=set(custom.values()),
        )
        while True:
            with _isolated_modules(
                candidates,
                original_modules,
                roots=set(custom.values()),
            ) as source_modules:
                identities = preflight_exporters(
                    plan,
                    source_modules=source_modules,
                )
                discovered = _recorded_exporter_modules(custom)
                if discovered <= candidates:
                    yield identities
                    return
                candidates.update(discovered)
                _include_loaded_source_dependencies(
                    candidates,
                    original_modules,
                    roots=set(custom.values()),
                )
            if len(candidates) > _MAX_EXPORTER_DEPENDENCIES + len(custom):
                name = next(iter(custom))
                raise OutputError(
                    f"exporter {name!r} has too many local modules to isolate",
                    code="exporter_identity_failed",
                    details={"exporter": name},
                )


def _recorded_exporter_modules(exporters: Mapping[str, str]) -> set[str]:
    lock, snapshots = _exporter_snapshot_state()
    with lock:
        return {
            module_name
            for name, root in exporters.items()
            for module_name in (snapshots[name].modules if name in snapshots else (root,))
        }


def _include_package_parents(names: set[str]) -> None:
    for name in tuple(names):
        parent = name.rpartition(".")[0]
        while parent:
            names.add(parent)
            parent = parent.rpartition(".")[0]


def _include_loaded_source_dependencies(
    names: set[str],
    modules: Mapping[str, Any],
    *,
    roots: set[str],
) -> None:
    try:
        package_distributions = importlib.metadata.packages_distributions()
    except Exception:
        package_distributions = {}
    selected_distributions = {
        distribution
        for root in roots
        for distribution in package_distributions.get(root.partition(".")[0], ())
    }
    _include_package_parents(names)
    pending = list(names)
    visited: set[str] = set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        module = modules.get(name)
        location = _source_location(name, module)
        if location is None or _is_installed_source(location[0]):
            continue
        for dependency in _source_imports(name, module):
            dependency_module = modules.get(dependency)
            dependency_location = _source_location(dependency, dependency_module)
            if dependency_location is None or not _is_local_source_name(
                dependency,
                selected_distributions=selected_distributions,
                package_distributions=package_distributions,
            ):
                continue
            candidate = dependency
            while candidate:
                if candidate not in names:
                    names.add(candidate)
                    pending.append(candidate)
                candidate = candidate.rpartition(".")[0]


def _source_imports(name: str, module: Any) -> set[str]:
    location = _source_location(name, module)
    if location is None:
        return set()
    origin, package = location
    try:
        source = SourceFileLoader(name, str(origin)).get_source(name)
        if source is None:
            return set()
        tree = ast.parse(source, filename=str(origin))
    except (OSError, SyntaxError, UnicodeError):
        return set()
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        imported = node.module or ""
        if node.level:
            if not package:
                continue
            with suppress(ImportError):
                imported = importlib.util.resolve_name(
                    f"{'.' * node.level}{imported}",
                    package,
                )
        if not imported or imported.startswith("."):
            continue
        imports.add(imported)
        imports.update(f"{imported}.{alias.name}" for alias in node.names if alias.name != "*")
    return imports


def _source_location(name: str, module: Any) -> tuple[Path, str] | None:
    origin = _module_origin(module)
    if origin is not None:
        package = str(getattr(module, "__package__", ""))
        return (origin, package) if origin.suffix == ".py" else None
    spec = _find_module_spec(name)
    if spec is None or not isinstance(spec.origin, str):
        return None
    origin = Path(spec.origin)
    if origin.suffix != ".py":
        return None
    package = name if spec.submodule_search_locations is not None else name.rpartition(".")[0]
    return origin, package


def _find_module_spec(name: str) -> Any:
    parent_name = name.rpartition(".")[0]
    if not parent_name:
        return PathFinder.find_spec(name)
    parent = sys.modules.get(parent_name)
    parent_spec = getattr(parent, "__spec__", None)
    if parent_spec is None:
        parent_spec = _find_module_spec(parent_name)
    if parent_spec is None or parent_spec.submodule_search_locations is None:
        return None
    return PathFinder.find_spec(name, parent_spec.submodule_search_locations)


def _is_installed_source(origin: Path) -> bool:
    return any(part in {"site-packages", "dist-packages"} for part in origin.parts)


def _is_local_source_name(
    name: str,
    *,
    selected_distributions: set[str],
    package_distributions: Mapping[str, list[str]],
) -> bool:
    top_level = name.partition(".")[0]
    if top_level == "marimo_export" or top_level in sys.stdlib_module_names:
        return False
    distributions = set(package_distributions.get(top_level, ()))
    return not distributions or bool(distributions & selected_distributions)


@contextmanager
def _isolated_modules(
    names: set[str],
    original_modules: Mapping[str, Any],
    *,
    roots: set[str],
) -> Iterator[frozenset[str]]:
    missing = object()
    try:
        package_distributions = importlib.metadata.packages_distributions()
    except Exception:
        package_distributions = {}
    package_attributes = {
        name: dict(vars(module))
        for name, module in original_modules.items()
        if module is not None and hasattr(module, "__path__")
    }
    native_get_code = SourceFileLoader.get_code
    new_retained_packages: set[str] = set()
    source_finder: _IsolatedSourceFinder | None = None

    def get_code(loader: SourceFileLoader, fullname: str) -> Any:
        filename = loader.get_filename(fullname)
        return loader.source_to_code(loader.get_data(filename), filename)

    try:
        eviction_names = _reloadable_module_names(
            names,
            original_modules,
            roots=roots,
        )
        native_ancestors = _native_module_ancestors(original_modules)
        protected_names = {name for name in eviction_names if name in native_ancestors}
        shadow_names = {
            name for name in protected_names if _is_python_source_module(original_modules.get(name))
        }
        eviction_names.difference_update(protected_names)
        eviction_names.update(shadow_names)
        for name in sorted(
            eviction_names,
            key=lambda value: value.count("."),
            reverse=True,
        ):
            sys.modules.pop(name, None)
            parent_name, separator, attribute = name.rpartition(".")
            parent = sys.modules.get(parent_name) if separator else None
            if parent is not None:
                with suppress(AttributeError):
                    delattr(parent, attribute)
        importlib.invalidate_caches()
        cast(Any, SourceFileLoader).get_code = get_code
        if shadow_names:
            source_finder = _IsolatedSourceFinder(
                {name: original_modules[name] for name in shadow_names}
            )
            sys.meta_path.insert(0, source_finder)
        yield frozenset(shadow_names)
    finally:
        if source_finder is not None:
            with suppress(ValueError):
                sys.meta_path.remove(source_finder)
        cast(Any, SourceFileLoader).get_code = native_get_code
        selected_distributions = {
            distribution
            for name in roots
            if _is_reloadable_module(sys.modules.get(name))
            for distribution in package_distributions.get(name.partition(".")[0], ())
        }
        new_retained_packages = _new_native_package_roots(
            roots=roots,
            original_modules=original_modules,
            modules=sys.modules,
        )
        rollback_names = _reloadable_module_names(
            names,
            sys.modules,
            roots=roots,
        )
        rollback_names.update(
            name
            for name in set(sys.modules) - set(original_modules)
            if _is_local_source_module(
                name,
                sys.modules[name],
                selected_distributions=selected_distributions,
                package_distributions=package_distributions,
            )
        )
        rollback_names = {
            name for name in rollback_names if name.partition(".")[0] not in new_retained_packages
        }
        for name in sorted(
            rollback_names,
            key=lambda value: value.count("."),
            reverse=True,
        ):
            sys.modules.pop(name, None)
        for name in sorted(rollback_names, key=lambda value: value.count(".")):
            original = original_modules.get(name, missing)
            if original is not missing:
                sys.modules[name] = original
        for name in sorted(rollback_names):
            parent_name, separator, attribute = name.rpartition(".")
            if not separator:
                continue
            parent = original_modules.get(parent_name)
            if parent is None:
                continue
            original = package_attributes.get(parent_name, {}).get(attribute, missing)
            if original is missing:
                with suppress(AttributeError):
                    delattr(parent, attribute)
            else:
                setattr(parent, attribute, original)
        importlib.invalidate_caches()


def _reloadable_module_names(
    names: set[str],
    modules: Mapping[str, Any],
    *,
    roots: set[str],
) -> set[str]:
    reloadable = {name for name in names if _is_reloadable_module(modules.get(name))}
    reloadable_roots = {root for root in roots if _is_reloadable_module(modules.get(root))}
    refresh_packages = {root.partition(".")[0] for root in reloadable_roots}
    for root in roots:
        if root in reloadable_roots:
            continue
        candidate = root
        while candidate:
            if candidate.partition(".")[0] not in refresh_packages:
                reloadable.discard(candidate)
            candidate = candidate.rpartition(".")[0]
    return reloadable


def _is_reloadable_module(module: Any) -> bool:
    return module is None or _is_python_source_module(module) or _is_namespace_package(module)


def _is_namespace_package(module: Any) -> bool:
    spec = getattr(module, "__spec__", None)
    return spec is not None and spec.origin is None and spec.submodule_search_locations is not None


def _new_native_package_roots(
    *,
    roots: set[str],
    original_modules: Mapping[str, Any],
    modules: Mapping[str, Any],
) -> set[str]:
    native_packages = {
        name.partition(".")[0]
        for name, module in modules.items()
        if name not in original_modules and not _is_reloadable_module(module)
    }
    return {
        package
        for root in roots
        if (package := root.partition(".")[0]) not in original_modules
        and package in native_packages
    }


def _native_module_ancestors(modules: Mapping[str, Any]) -> set[str]:
    ancestors: set[str] = set()
    for name, module in modules.items():
        if _is_reloadable_module(module):
            continue
        parent = name.rpartition(".")[0]
        while parent:
            ancestors.add(parent)
            parent = parent.rpartition(".")[0]
    return ancestors


class _IsolatedSourceLoader(SourceFileLoader):
    def exec_module(self, module: Any) -> None:
        prefix = f"{module.__name__}."
        for child_name, child in tuple(sys.modules.items()):
            if not child_name.startswith(prefix):
                continue
            attribute = child_name[len(prefix) :]
            if "." not in attribute:
                setattr(module, attribute, child)
        super().exec_module(module)


class _IsolatedSourceFinder:
    def __init__(self, modules: Mapping[str, Any]) -> None:
        self._modules = modules

    def find_spec(
        self,
        fullname: str,
        path: Any = None,
        target: Any = None,
    ) -> Any:
        del path, target
        module = self._modules.get(fullname)
        origin = _module_origin(module)
        native_spec = getattr(module, "__spec__", None)
        if origin is None or native_spec is None:
            return None
        locations = native_spec.submodule_search_locations
        return importlib.util.spec_from_file_location(
            fullname,
            origin,
            loader=_IsolatedSourceLoader(fullname, str(origin)),
            submodule_search_locations=list(locations) if locations is not None else None,
        )


def _is_python_source_module(module: Any) -> bool:
    origin = _module_origin(module)
    return origin is not None and origin.suffix == ".py"


def _is_local_source_module(
    name: str,
    module: Any,
    *,
    selected_distributions: set[str],
    package_distributions: Mapping[str, list[str]],
) -> bool:
    if not _is_python_source_module(module):
        return False
    top_level = name.partition(".")[0]
    if top_level == "marimo_export" or top_level in sys.stdlib_module_names:
        return False
    distributions = set(package_distributions.get(top_level, ()))
    if not distributions:
        return True
    return bool(distributions & selected_distributions)


def _record_exporter_snapshot(
    *,
    output: str,
    name: str,
    module: Any,
    identity: _ExporterIdentity,
) -> None:
    lock, snapshots = _exporter_snapshot_state()
    with lock:
        previous = snapshots.get(name)
        if (
            previous is not None
            and previous.module() is module
            and previous.environment != identity.environment
            and previous.runtime == identity.runtime
        ):
            raise OutputError(
                f"output {output!r} exporter {name!r} changed while its loaded module stayed stale",
                code="exporter_stale",
                details={"output": output, "exporter": name},
            )
        snapshots[name] = _ExporterSnapshot(
            module=weakref.ref(module),
            runtime=identity.runtime,
            environment=identity.environment,
            modules=identity.modules,
        )


def _exporter_snapshot_state() -> tuple[LockType, dict[str, _ExporterSnapshot]]:
    try:
        from marimo._runtime.context import get_context

        context = get_context()
    except Exception:
        return _EXPORTER_SNAPSHOT_LOCK, _EXPORTER_SNAPSHOTS
    owner = context.app_kernel_runner_registry
    state = getattr(owner, _EXPORTER_SNAPSHOT_STATE_ATTRIBUTE, None)
    if (
        isinstance(state, tuple)
        and len(state) == 2
        and isinstance(state[0], LockType)
        and isinstance(state[1], dict)
    ):
        return cast(tuple[LockType, dict[str, _ExporterSnapshot]], state)
    created: tuple[LockType, dict[str, _ExporterSnapshot]] = (
        threading.Lock(),
        {},
    )
    setattr(owner, _EXPORTER_SNAPSHOT_STATE_ATTRIBUTE, created)
    return created


def _direct_callable_code(value: Any) -> Any | None:
    if inspect.ismethod(value):
        value = value.__func__
    if inspect.isfunction(value):
        return value.__code__
    candidate = inspect.getattr_static(value, "__code__", None)
    return candidate if inspect.iscode(candidate) else None


def _callable_code(value: Any) -> Any | None:
    candidate = _direct_callable_code(value)
    if candidate is not None:
        return candidate
    call = inspect.getattr_static(type(value), "__call__", None)
    if isinstance(call, (classmethod, staticmethod)):
        call = call.__func__
    if inspect.isfunction(call):
        return call.__code__
    candidate = inspect.getattr_static(call, "__code__", None)
    return candidate if inspect.iscode(candidate) else None


def _exporter_identity(
    *,
    name: str,
    module: Any,
    value: Any,
    distributions: tuple[str, ...],
    package_distributions: Mapping[str, list[str]],
) -> _ExporterIdentity:
    dependencies, dependency_modules, isolation_modules = _exporter_dependencies(
        value,
        module,
        package_distributions=package_distributions,
    )
    common: JsonObject = {
        "name": name,
        "module": str(getattr(module, "__name__", "")),
        "symbol_type": f"{type(value).__module__}.{type(value).__qualname__}",
    }
    runtime_dependencies = {
        key: value for key, value in dependencies.items() if not key.startswith("module:")
    }
    environment_dependencies = {
        key: value for key, value in dependencies.items() if key.startswith("module:")
    }
    payload: JsonObject = {**common, "dependencies": dependencies}
    runtime_payload: JsonObject = {
        **common,
        "dependencies": runtime_dependencies,
    }
    environment_payload: JsonObject = {
        **common,
        "dependencies": environment_dependencies,
    }
    origin = getattr(getattr(module, "__spec__", None), "origin", None)
    if isinstance(origin, str) and origin not in {"built-in", "frozen"}:
        module_digest = _file_digest(Path(origin))
        if module_digest is not None:
            payload["module_sha256"] = module_digest
            environment_payload["module_sha256"] = module_digest
    code = _callable_code(value)
    if code is not None:
        from marimo._save.hash import hash_module

        with suppress(TypeError, ValueError):
            callable_digest = hash_module(code).hex()
            payload["callable_sha256"] = callable_digest
            runtime_payload["callable_sha256"] = callable_digest
    try:
        source = inspect.getsource(value)
    except (OSError, TypeError):
        source = None
    if source is not None:
        source_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        payload["source_sha256"] = source_digest
        environment_payload["source_sha256"] = source_digest

    package_names = set(distributions)
    top_level = str(getattr(module, "__name__", "")).partition(".")[0]
    if top_level:
        package_names.update(package_distributions.get(top_level, ()))
    for dependency_module in dependency_modules:
        dependency_top_level = dependency_module.partition(".")[0]
        if dependency_top_level:
            package_names.update(package_distributions.get(dependency_top_level, ()))
    versions: JsonObject = {}
    for distribution in sorted(package_names):
        with suppress(importlib.metadata.PackageNotFoundError):
            versions[distribution] = importlib.metadata.version(distribution)
    if versions:
        payload["distributions"] = versions
        environment_payload["distributions"] = versions
    return _ExporterIdentity(
        cache=hashlib.sha256(canonical_bytes(payload)).hexdigest(),
        runtime=hashlib.sha256(canonical_bytes(runtime_payload)).hexdigest(),
        environment=hashlib.sha256(canonical_bytes(environment_payload)).hexdigest(),
        modules=tuple(sorted(isolation_modules)),
    )


def _exporter_dependencies(
    value: Any,
    module: Any,
    *,
    package_distributions: Mapping[str, list[str]],
) -> tuple[JsonObject, frozenset[str], frozenset[str]]:
    records: JsonObject = {}
    module_names: set[str] = set()
    isolation_module_names: set[str] = set()
    recorded_modules: set[int] = set()
    expanded_modules: set[int] = set()
    visited_callables: set[int] = set()
    resolved_imports: dict[str, Any | None] = {}
    selected_owner = sys.modules.get(str(getattr(value, "__module__", "")))
    selected_distributions: set[str] = set()
    for selected_module in (module, selected_owner):
        selected_top_level = str(getattr(selected_module, "__name__", "")).partition(".")[0]
        selected_distributions.update(package_distributions.get(selected_top_level, ()))

    def is_local_module(dependency: Any) -> bool:
        origin = _module_origin(dependency)
        if origin is None:
            return False
        top_level = str(getattr(dependency, "__name__", "")).partition(".")[0]
        if not top_level or top_level == "marimo_export" or top_level in sys.stdlib_module_names:
            return False
        dependency_distributions = package_distributions.get(top_level)
        if not dependency_distributions:
            return True
        return bool(selected_distributions.intersection(dependency_distributions))

    def record(name: str, digest: str) -> None:
        if name in records:
            return
        if len(records) >= _MAX_EXPORTER_DEPENDENCIES:
            raise ValueError(
                f"exporter dependency graph exceeds {_MAX_EXPORTER_DEPENDENCIES} entries"
            )
        records[name] = digest

    def visit_module(
        dependency: Any,
        *,
        expand: bool,
    ) -> None:
        identifier = id(dependency)
        name = str(getattr(dependency, "__name__", ""))
        if identifier not in recorded_modules:
            recorded_modules.add(identifier)
            if name:
                module_names.add(name)
                if dependency is module or is_local_module(dependency):
                    isolation_module_names.add(name)
            origin = _module_origin(dependency)
            if origin is not None:
                digest = _file_digest(origin)
                if digest is not None:
                    record(f"module:{name}", digest)
        if not is_local_module(dependency):
            return
        if not expand or identifier in expanded_modules:
            return
        expanded_modules.add(identifier)
        namespace = getattr(dependency, "__dict__", {})
        for attribute in sorted(namespace):
            member = namespace[attribute]
            if inspect.ismodule(member):
                if is_local_module(member):
                    visit_module(
                        member,
                        expand=True,
                    )
                continue
            if inspect.isfunction(member):
                owner = sys.modules.get(str(getattr(member, "__module__", "")))
                if owner is dependency or (owner is not None and is_local_module(owner)):
                    visit_callable(member)
                continue
            if inspect.isclass(member):
                owner = sys.modules.get(str(getattr(member, "__module__", "")))
                if owner is dependency or (owner is not None and is_local_module(owner)):
                    visit_callable(member)

    def referenced_attributes(code: Any, name: str) -> frozenset[str]:
        attributes: set[str] = set()
        instructions = tuple(dis.get_instructions(code))
        saw_reference = False
        saw_dynamic_reference = False
        for index, instruction in enumerate(instructions[:-1]):
            if (
                instruction.opname
                not in {
                    "LOAD_GLOBAL",
                    "LOAD_NAME",
                    "LOAD_DEREF",
                    "LOAD_FAST",
                }
                or instruction.argval != name
            ):
                continue
            saw_reference = True
            following = instructions[index + 1]
            if following.opname in {"LOAD_ATTR", "LOAD_METHOD"} and isinstance(
                following.argval, str
            ):
                attributes.add(following.argval)
                continue
            if (
                following.opname == "LOAD_CONST"
                and isinstance(following.argval, str)
                and index > 0
                and instructions[index - 1].opname in {"LOAD_GLOBAL", "LOAD_NAME"}
                and instructions[index - 1].argval == "getattr"
            ):
                attributes.add(following.argval)
                continue
            saw_dynamic_reference = True
        if saw_reference and saw_dynamic_reference:
            attributes.add("*")
        return frozenset(attributes)

    def visit_code_globals(dependency: Any, code: Any) -> None:
        owner = sys.modules.get(str(getattr(dependency, "__module__", "")))
        namespace = getattr(owner, "__dict__", {}) if owner is not None else {}
        names = {
            instruction.argval
            for instruction in dis.get_instructions(code)
            if instruction.opname in {"LOAD_GLOBAL", "LOAD_NAME"}
            and isinstance(instruction.argval, str)
        }
        module_name = str(getattr(dependency, "__module__", ""))
        qualname = str(
            getattr(
                dependency,
                "__qualname__",
                getattr(dependency, "__name__", type(dependency).__qualname__),
            )
        )
        for name in sorted(names):
            if name not in namespace:
                continue
            referenced = namespace[name]
            visit_reference(
                referenced,
                f"code-value:{module_name}:{qualname}:{code.co_name}:{name}",
                module_members=(
                    referenced_attributes(code, name) if inspect.ismodule(referenced) else None
                ),
            )
        for constant in code.co_consts:
            if inspect.iscode(constant):
                visit_code_globals(dependency, constant)

    def visit_module_members(
        dependency: Any,
        members: frozenset[str],
        *,
        label: str,
    ) -> None:
        visit_module(dependency, expand=False)
        if "*" in members:
            visit_module(dependency, expand=True)
            return
        module_name = str(getattr(dependency, "__name__", ""))
        namespace = getattr(dependency, "__dict__", {})
        for member_name in sorted(members):
            member = namespace.get(member_name)
            if member is None and module_name:
                candidate = f"{module_name}.{member_name}"
                if candidate not in resolved_imports:
                    try:
                        resolved_imports[candidate] = importlib.import_module(candidate)
                    except Exception:
                        resolved_imports[candidate] = None
                member = resolved_imports[candidate]
            if member is None:
                continue
            visit_reference(
                member,
                f"{label}:{member_name}",
            )

    def visit_imports(dependency: Any, code: Any) -> None:
        module_name = str(getattr(dependency, "__module__", ""))
        owner = sys.modules.get(module_name)
        package = str(getattr(owner, "__package__", "")) if owner is not None else ""
        instructions = tuple(dis.get_instructions(code))
        for index, instruction in enumerate(instructions):
            if instruction.opname != "IMPORT_NAME" or not isinstance(instruction.argval, str):
                continue
            level = 0
            fromlist: tuple[str, ...] = ()
            if index >= 2:
                level_value = instructions[index - 2].argval
                fromlist_value = instructions[index - 1].argval
                if isinstance(level_value, int):
                    level = level_value
                if isinstance(fromlist_value, tuple) and all(
                    isinstance(item, str) for item in fromlist_value
                ):
                    fromlist = fromlist_value
            imported_name = instruction.argval
            if level:
                if not package:
                    continue
                try:
                    imported_name = importlib.util.resolve_name(
                        f"{'.' * level}{imported_name}",
                        package,
                    )
                except (ImportError, ValueError):
                    continue
            if not imported_name:
                continue
            if imported_name not in resolved_imports:
                try:
                    resolved_imports[imported_name] = importlib.import_module(imported_name)
                except Exception:
                    resolved_imports[imported_name] = None
            imported = resolved_imports[imported_name]
            if imported is None:
                continue
            members: frozenset[str]
            if fromlist:
                members = frozenset(member for member in fromlist if isinstance(member, str))
            else:
                aliases: set[str] = set()
                for following in instructions[index + 1 : index + 8]:
                    if following.opname in {"STORE_FAST", "STORE_NAME"} and isinstance(
                        following.argval, str
                    ):
                        aliases.add(following.argval)
                        break
                    if following.opname in {
                        "IMPORT_NAME",
                        "POP_TOP",
                        "RETURN_VALUE",
                        "RETURN_CONST",
                    }:
                        break
                direct_members = {
                    name
                    for name in code.co_names
                    if isinstance(name, str) and name in vars(imported)
                }
                for alias in aliases:
                    direct_members.update(referenced_attributes(code, alias))
                members = frozenset(direct_members)
            visit_module_members(
                imported,
                members,
                label=f"import:{module_name}:{imported_name}",
            )
        for constant in code.co_consts:
            if inspect.iscode(constant):
                visit_imports(dependency, constant)

    def visit_callable(dependency: Any) -> None:
        if inspect.ismethod(dependency):
            dependency = dependency.__func__
        identifier = id(dependency)
        if identifier in visited_callables:
            return
        visited_callables.add(identifier)
        module_name = str(getattr(dependency, "__module__", ""))
        owner = sys.modules.get(module_name)
        qualname = str(
            getattr(
                dependency,
                "__qualname__",
                getattr(dependency, "__name__", type(dependency).__qualname__),
            )
        )
        if owner is not None:
            visit_module(owner, expand=False)
        code = _direct_callable_code(dependency)
        if code is None and inspect.isclass(dependency):
            if owner is None or (owner is not module and not is_local_module(owner)):
                return
            for attribute in sorted(vars(dependency)):
                if attribute.startswith("__"):
                    continue
                member = inspect.getattr_static(dependency, attribute)
                if isinstance(member, (classmethod, staticmethod)):
                    member = member.__func__
                if callable(member):
                    visit_callable(member)
                    continue
                if inspect.isdatadescriptor(member) or inspect.ismethoddescriptor(member):
                    continue
                try:
                    portable = _exporter_value_identity(
                        member,
                        f"class-value:{module_name}:{qualname}:{attribute}",
                    )
                except TypeError:
                    continue
                record(
                    f"class-value:{module_name}:{qualname}:{attribute}",
                    hashlib.sha256(canonical_bytes(portable)).hexdigest(),
                )
            return
        if code is None:
            instance_state = getattr(dependency, "__dict__", None)
            if isinstance(instance_state, dict) and instance_state:
                try:
                    portable = _exporter_value_identity(
                        instance_state,
                        f"callable-state:{module_name}:{qualname}",
                    )
                except TypeError:
                    for attribute, member in sorted(instance_state.items()):
                        visit_reference(
                            member,
                            f"callable-value:{module_name}:{qualname}:{attribute}",
                        )
                else:
                    record(
                        f"callable-state:{module_name}:{qualname}",
                        hashlib.sha256(canonical_bytes(portable)).hexdigest(),
                    )
            call = inspect.getattr_static(type(dependency), "__call__", None)
            if inspect.isfunction(call):
                visit_callable(call)
            return
        from marimo._save.hash import hash_module

        record(f"callable:{module_name}:{qualname}", hash_module(code).hex())
        if owner is not None and owner is not module and not is_local_module(owner):
            return
        visit_imports(dependency, code)
        visit_code_globals(dependency, code)
        defaults = getattr(dependency, "__defaults__", None)
        if isinstance(defaults, tuple):
            for index, default in enumerate(defaults):
                visit_reference(
                    default,
                    f"default:{module_name}:{qualname}:{index}",
                )
        keyword_defaults = getattr(dependency, "__kwdefaults__", None)
        if isinstance(keyword_defaults, dict):
            for default_name, default in sorted(keyword_defaults.items()):
                visit_reference(
                    default,
                    f"keyword-default:{module_name}:{qualname}:{default_name}",
                )
        try:
            closure = inspect.getclosurevars(dependency)
        except TypeError:
            return
        references = {**closure.globals, **closure.nonlocals}
        for dependency_name, referenced in sorted(references.items()):
            visit_reference(
                referenced,
                f"value:{module_name}:{qualname}:{dependency_name}",
                module_members=(
                    referenced_attributes(code, dependency_name)
                    if inspect.ismodule(referenced)
                    else None
                ),
            )

    def visit_reference(
        dependency: Any,
        label: str,
        *,
        module_members: frozenset[str] | None = None,
    ) -> None:
        if inspect.ismodule(dependency):
            if module_members is None:
                visit_module(dependency, expand=True)
            else:
                visit_module_members(
                    dependency,
                    module_members,
                    label=label,
                )
            return
        if (
            inspect.isfunction(dependency)
            or inspect.ismethod(dependency)
            or inspect.isclass(dependency)
        ):
            visit_callable(dependency)
            return
        if inspect.isbuiltin(dependency):
            module_name = str(getattr(dependency, "__module__", ""))
            owner = sys.modules.get(module_name)
            if owner is not None:
                visit_module(owner, expand=False)
            portable: JsonValue = {
                "type": "builtin-function",
                "module": module_name,
                "name": str(
                    getattr(
                        dependency,
                        "__qualname__",
                        getattr(dependency, "__name__", ""),
                    )
                ),
            }
            record(label, hashlib.sha256(canonical_bytes(portable)).hexdigest())
            return
        if callable(dependency):
            visit_callable(dependency)
            return
        try:
            portable = _exporter_value_identity(dependency, label)
        except TypeError:
            owner = sys.modules.get(str(getattr(type(dependency), "__module__", "")))
            if owner is not None:
                visit_module(owner, expand=False)
            call = inspect.getattr_static(type(dependency), "__call__", None)
            if inspect.isfunction(call):
                visit_callable(call)
            return
        record(label, hashlib.sha256(canonical_bytes(portable)).hexdigest())

    visit_module(module, expand=False)
    visit_callable(value)
    return (
        records,
        frozenset(module_names),
        frozenset(isolation_module_names),
    )


def _module_origin(module: Any) -> Path | None:
    origin = getattr(getattr(module, "__spec__", None), "origin", None)
    if not isinstance(origin, str) or origin in {"built-in", "frozen"}:
        origin = getattr(module, "__file__", None)
    if not isinstance(origin, str):
        return None
    try:
        path = Path(origin).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return path if path.is_file() else None


def _file_digest(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(64 * 1024):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


__all__ = ["preflight_exporters", "prepared_exporters"]
