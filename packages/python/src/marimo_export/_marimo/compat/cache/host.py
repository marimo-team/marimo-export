"""Keep Marimo cache restores compatible with interactive host sessions."""

from __future__ import annotations

from collections.abc import Callable
from io import BytesIO
from typing import Any, cast

from marimo_export._marimo.compat.cache.patch import (
    _PROCESS_PATCH_LOCK,
    _CloseHandle,
    native_cache_contract,
)
from marimo_export.errors import CompatibilityError

_UI_ELEMENT_STUB = "marimo._save.stubs.ui_element_stub.UIElementStub"
_POLARS_TYPES = (
    "polars.dataframe.frame.DataFrame",
    "polars.series.series.Series",
)


class _OwnedLoader(str):
    pass


class _HostPatchCoordinator:
    def __init__(self) -> None:
        self._tokens: set[object] = set()
        self._lifecycle_owner: Any = None
        self._original_ui_check: Any = None
        self._patched_ui_check: Any = None
        self._original_tensor_buffer: Any = None
        self._patched_tensor_buffer: Any = None
        self._original_polars_loaders: dict[str, str | None] = {}
        self._polars_loader: _OwnedLoader | None = None

    def open(self) -> _CloseHandle:
        from marimo._save import encode
        from marimo._save.stubs.lazy_stub import LAZY_STUB_LOOKUP

        token = object()
        with _PROCESS_PATCH_LOCK:
            if self._tokens:
                self._require_installed(encode, LAZY_STUB_LOOKUP)
            else:
                _, lifecycle, _ = native_cache_contract()
                self._lifecycle_owner = lifecycle
                self._original_ui_check = lifecycle._restored_ui_defs
                self._original_tensor_buffer = encode._contiguous_tensor_bytes
                self._original_polars_loaders = {
                    type_name: LAZY_STUB_LOOKUP.get(type_name) for type_name in _POLARS_TYPES
                }
                self._polars_loader = _OwnedLoader("pickle")
                self._patched_ui_check = _ui_check(self._original_ui_check)
                self._patched_tensor_buffer = _tensor_buffer(self._original_tensor_buffer)
                installed: list[str] = []
                try:
                    lifecycle._restored_ui_defs = staticmethod(self._patched_ui_check)
                    installed.append("ui")
                    cast(Any, encode)._contiguous_tensor_bytes = self._patched_tensor_buffer
                    installed.append("tensor")
                    installed.append("polars")
                    for type_name in _POLARS_TYPES:
                        LAZY_STUB_LOOKUP[type_name] = self._polars_loader
                except BaseException:
                    self._restore(installed, encode, LAZY_STUB_LOOKUP)
                    self._clear()
                    raise
            self._tokens.add(token)
        return _CloseHandle(lambda: self._release(token))

    def _release(self, token: object) -> None:
        import marimo._runtime.executor.lifecycles.cached as cached_lifecycle
        from marimo._save import encode
        from marimo._save.stubs.lazy_stub import LAZY_STUB_LOOKUP

        with _PROCESS_PATCH_LOCK:
            if token not in self._tokens:
                raise RuntimeError("unbalanced host cache patch release")
            owns_ui = (
                getattr(self._lifecycle_owner, "_restored_ui_defs", None) is self._patched_ui_check
            )
            owns_tensor = encode._contiguous_tensor_bytes is self._patched_tensor_buffer
            active_lifecycle = getattr(cached_lifecycle, "CachedLifecycle", None)
            owns_active_resolution = (
                getattr(active_lifecycle, "_restored_ui_defs", None) is self._patched_ui_check
            )
            owned_polars = {
                name for name in _POLARS_TYPES if LAZY_STUB_LOOKUP.get(name) is self._polars_loader
            }
            conflict = not (
                owns_ui
                and owns_tensor
                and owns_active_resolution
                and len(owned_polars) == len(_POLARS_TYPES)
            )
            self._tokens.remove(token)
            if self._tokens:
                if conflict:
                    _raise_conflict()
                return
            if owns_ui:
                self._lifecycle_owner._restored_ui_defs = staticmethod(self._original_ui_check)
            if owns_tensor:
                encode._contiguous_tensor_bytes = self._original_tensor_buffer
            for name in owned_polars:
                original = self._original_polars_loaders[name]
                if original is None:
                    LAZY_STUB_LOOKUP.pop(name, None)
                else:
                    LAZY_STUB_LOOKUP[name] = original
            self._clear()
            if conflict:
                _raise_conflict()

    def native_contract(self) -> tuple[Any, Any, dict[str, str | None]]:
        from marimo._save import encode
        from marimo._save.stubs.lazy_stub import LAZY_STUB_LOOKUP

        with _PROCESS_PATCH_LOCK:
            if self._tokens:
                self._require_installed(encode, LAZY_STUB_LOOKUP)
                return (
                    self._original_ui_check,
                    self._original_tensor_buffer,
                    dict(self._original_polars_loaders),
                )
            _, lifecycle, _ = native_cache_contract()
            return (
                getattr(lifecycle, "_restored_ui_defs", None),
                getattr(encode, "_contiguous_tensor_bytes", None),
                {name: LAZY_STUB_LOOKUP.get(name) for name in _POLARS_TYPES},
            )

    def _require_installed(self, encode: Any, loaders: dict[str, str]) -> None:
        import marimo._runtime.executor.lifecycles.cached as cached_lifecycle

        active_lifecycle = getattr(cached_lifecycle, "CachedLifecycle", None)
        if (
            getattr(self._lifecycle_owner, "_restored_ui_defs", None) is not self._patched_ui_check
            or getattr(active_lifecycle, "_restored_ui_defs", None) is not self._patched_ui_check
            or getattr(encode, "_contiguous_tensor_bytes", None) is not self._patched_tensor_buffer
            or any(loaders.get(name) is not self._polars_loader for name in _POLARS_TYPES)
        ):
            _raise_conflict()

    def _restore(self, installed: list[str], encode: Any, loaders: dict[str, str]) -> None:
        if "polars" in installed:
            for name, original in self._original_polars_loaders.items():
                if loaders.get(name) is not self._polars_loader:
                    continue
                if original is None:
                    loaders.pop(name, None)
                else:
                    loaders[name] = original
        if "tensor" in installed:
            encode._contiguous_tensor_bytes = self._original_tensor_buffer
        if "ui" in installed:
            self._lifecycle_owner._restored_ui_defs = staticmethod(self._original_ui_check)

    def _clear(self) -> None:
        self._lifecycle_owner = None
        self._original_ui_check = None
        self._patched_ui_check = None
        self._original_tensor_buffer = None
        self._patched_tensor_buffer = None
        self._original_polars_loaders = {}
        self._polars_loader = None


_HOST_PATCHES = _HostPatchCoordinator()


def keep_cached_cells_compatible() -> Callable[[], None]:
    """Install the cache repairs required by an interactive Marimo host."""

    from marimo_export._marimo.compat.cache.probe import require_cache_capabilities

    require_cache_capabilities()
    return _HOST_PATCHES.open().close


def native_host_cache_contract() -> tuple[Any, Any, dict[str, str | None]]:
    return _HOST_PATCHES.native_contract()


def _ui_check(original: Callable[[Any, dict[str, Any]], bool]) -> Callable[..., bool]:
    def restored_ui_defs(attempt: Any, glbls: dict[str, Any]) -> bool:
        return original(attempt, glbls) or _cached_result_contains_ui(attempt)

    return restored_ui_defs


def _tensor_buffer(original: Callable[[Any], memoryview]) -> Callable[[Any], memoryview]:
    def contiguous_tensor_bytes(value: Any) -> memoryview:
        if _type_name(value) in _POLARS_TYPES:
            frame = value.to_frame() if hasattr(value, "to_frame") else value
            buffer = BytesIO()
            frame.serialize(buffer)
            return memoryview(buffer.getvalue())
        return original(value)

    return contiguous_tensor_bytes


def _cached_result_contains_ui(attempt: Any) -> bool:
    return any(_contains_cached_ui(value) for value in attempt.defs.values()) or (
        _contains_cached_ui(attempt.meta.get("return"))
    )


def _contains_cached_ui(value: object, seen: set[int] | None = None) -> bool:
    from marimo._plugins.ui._core.ui_element import UIElement
    from marimo._save.stubs.lazy_stub import UnhashableStub
    from marimo._save.stubs.ui_element_stub import UIElementStub

    if isinstance(value, UIElement):
        return True
    if type(value) is UIElementStub:
        return True
    if type(value) is UnhashableStub:
        return value.type_name == _UI_ELEMENT_STUB
    if not isinstance(value, (dict, list, set, tuple)):
        return False
    if seen is None:
        seen = set()
    value_id = id(value)
    if value_id in seen:
        return False
    seen.add(value_id)
    values = value.values() if isinstance(value, dict) else value
    return any(_contains_cached_ui(item, seen) for item in values)


def _type_name(value: object) -> str:
    return f"{type(value).__module__}.{type(value).__name__}"


def _raise_conflict() -> None:
    raise CompatibilityError(
        "another owner replaced Marimo's host cache integration while marimo-export was using it",
        code="marimo_cache_patch_conflict",
    )


__all__ = ["keep_cached_cells_compatible", "native_host_cache_contract"]
