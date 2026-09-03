"""Freeze custom exporter implementations for one kernel process."""

from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
import re
import struct
import threading
import weakref
from _thread import LockType
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from marimo_export._json import JsonObject, JsonValue, canonical_bytes
from marimo_export.errors import OutputError

ExporterKey = tuple[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class ExporterIdentity:
    """Cache identity and frozen source modules for one exporter selection."""

    cache: str
    modules: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ModuleSnapshot:
    module: weakref.ReferenceType[ModuleType]
    record: JsonObject


@dataclass(frozen=True, slots=True)
class _ExporterSnapshot:
    module: weakref.ReferenceType[ModuleType]
    cache: str


@dataclass(slots=True)
class _IdentityState:
    lock: LockType
    modules: dict[str, _ModuleSnapshot]
    exporters: dict[ExporterKey, _ExporterSnapshot]


_GLOBAL_STATE = _IdentityState(threading.Lock(), {}, {})
_STATE_ATTRIBUTE = "_marimo_export_exporter_identity_state"


def freeze_exporter_identity(
    *,
    output: str,
    key: ExporterKey,
    module: ModuleType,
    value: Any,
    modules: Mapping[str, ModuleType],
    distributions: tuple[str, ...],
    package_distributions: Mapping[str, list[str]],
) -> ExporterIdentity:
    """Freeze loaded sources and return their deterministic cache identity."""

    current = {name: _module_record(name, dependency) for name, dependency in modules.items()}
    state = _identity_state()
    with state.lock:
        for name, dependency in modules.items():
            previous = state.modules.get(name)
            if previous is None:
                state.modules[name] = _ModuleSnapshot(
                    module=weakref.ref(dependency),
                    record=current[name],
                )
                continue
            if previous.module() is not dependency or previous.record != current[name]:
                raise _source_changed(output, key[0], name)

        payload: JsonObject = {
            "name": key[0],
            "dependencies": list(key[1]),
            "modules": {name: current[name] for name in sorted(current)},
            "callable": _callable_identity(value),
        }
        versions = _distribution_versions(
            modules,
            distributions=distributions,
            package_distributions=package_distributions,
        )
        if versions:
            payload["distributions"] = versions
        cache = hashlib.sha256(canonical_bytes(payload)).hexdigest()
        previous_exporter = state.exporters.get(key)
        if previous_exporter is not None and previous_exporter.module() is not module:
            raise _source_changed(output, key[0], module.__name__)
        state.exporters[key] = _ExporterSnapshot(module=weakref.ref(module), cache=cache)
    return ExporterIdentity(cache=cache, modules=tuple(sorted(modules)))


def verify_exporter_sources(
    *,
    output: str,
    key: ExporterKey,
    modules: Mapping[str, ModuleType],
) -> None:
    """Reject source edits made after the exporter was frozen."""

    state = _identity_state()
    current = {name: _module_record(name, module) for name, module in modules.items()}
    with state.lock:
        for name, module in modules.items():
            previous = state.modules.get(name)
            if (
                previous is None
                or previous.module() is not module
                or previous.record != current[name]
            ):
                raise _source_changed(output, key[0], name)


def module_origin(module: ModuleType) -> Path | None:
    """Return the resolved file backing a loaded module."""

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


def _module_record(name: str, module: ModuleType) -> JsonObject:
    origin = module_origin(module)
    if origin is None:
        return {"name": name, "kind": "namespace"}
    return {
        "name": name,
        "kind": "source" if origin.suffix == ".py" else "binary",
        "sha256": _stable_file_digest(origin),
    }


def _stable_file_digest(path: Path) -> str:
    for _ in range(3):
        before = path.stat()
        payload = path.read_bytes()
        after = path.stat()
        if _revision(before) == _revision(after) and len(payload) == after.st_size:
            return hashlib.sha256(payload).hexdigest()
    raise RuntimeError(f"module source {path.name!r} changed while it was read")


def _revision(value: Any) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _callable_identity(value: Any) -> JsonObject:
    result: JsonObject = {
        "type": f"{type(value).__module__}.{type(value).__qualname__}",
    }
    code = _callable_code(value)
    if code is not None:
        from marimo._save.hash import hash_module

        result["code_sha256"] = hash_module(code).hex()
    defaults = getattr(value, "__defaults__", None)
    if defaults:
        result["defaults"] = _exporter_value_identity(defaults, "exporter defaults")
    keyword_defaults = getattr(value, "__kwdefaults__", None)
    if keyword_defaults:
        result["keyword_defaults"] = _exporter_value_identity(
            keyword_defaults,
            "exporter keyword defaults",
        )
    state = getattr(value, "__dict__", None)
    if isinstance(state, dict) and state:
        result["state"] = _exporter_value_identity(state, "exporter state")
    return result


def _callable_code(value: Any) -> Any | None:
    if inspect.ismethod(value):
        value = value.__func__
    if inspect.isfunction(value):
        return value.__code__
    call = inspect.getattr_static(type(value), "__call__", None)
    if isinstance(call, (classmethod, staticmethod)):
        call = call.__func__
    return call.__code__ if inspect.isfunction(call) else None


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


def _distribution_versions(
    modules: Mapping[str, ModuleType],
    *,
    distributions: tuple[str, ...],
    package_distributions: Mapping[str, list[str]],
) -> JsonObject:
    names = set(distributions)
    for module_name in modules:
        names.update(package_distributions.get(module_name.partition(".")[0], ()))
    versions: JsonObject = {}
    for distribution in sorted(names):
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            continue
    return versions


def _source_changed(output: str, exporter: str, module: str) -> OutputError:
    return OutputError(
        f"output {output!r} exporter {exporter!r} source changed; restart the session",
        code="exporter_source_changed",
        details={"output": output, "exporter": exporter, "module": module},
    )


def _identity_state() -> _IdentityState:
    try:
        from marimo._runtime.context import get_context

        owner = get_context().app_kernel_runner_registry
    except Exception:
        return _GLOBAL_STATE
    state = getattr(owner, _STATE_ATTRIBUTE, None)
    if isinstance(state, _IdentityState):
        return state
    created = _IdentityState(threading.Lock(), {}, {})
    setattr(owner, _STATE_ATTRIBUTE, created)
    return created


__all__ = [
    "ExporterIdentity",
    "ExporterKey",
    "freeze_exporter_identity",
    "module_origin",
    "verify_exporter_sources",
]
