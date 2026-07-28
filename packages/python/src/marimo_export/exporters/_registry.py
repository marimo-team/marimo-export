from __future__ import annotations

import importlib
import importlib.util
import inspect
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeAlias, cast

from marimo_export._json import JsonObject
from marimo_export._python import validate_import_reference
from marimo_export.projection import Projection

_MAX_SAFE_INTEGER = 2**53 - 1
_PARQUET_COMPRESSIONS = frozenset({"NONE", "SNAPPY", "GZIP", "BROTLI", "LZ4", "ZSTD"})
_ExporterResult: TypeAlias = Projection | Awaitable[Projection]
_Exporter: TypeAlias = Callable[..., _ExporterResult]
_OptionNormalizer: TypeAlias = Callable[[Mapping[str, object], str], JsonObject]


@dataclass(frozen=True)
class _BuiltinExporter:
    name: str
    reference: str
    version: str
    normalize_options: _OptionNormalizer
    extra: str | None = None
    availability_modules: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return all(importlib.util.find_spec(name) is not None for name in self.availability_modules)


@dataclass(frozen=True)
class _ResolvedExporter:
    function: _Exporter
    reference: str
    version: str | None

    async def project(self, value: object, options: Mapping[str, object]) -> Projection:
        result = self.function(value, **dict(options))
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, Projection):
            raise TypeError(f"exporter {self.reference!r} must return Projection")
        return result


def _optionless(options: Mapping[str, object], path: str) -> JsonObject:
    _keys(options, set(), path)
    return {}


def _json(options: Mapping[str, object], path: str) -> JsonObject:
    _keys(options, {"indent", "sort_keys"}, path)
    indent = options.get("indent")
    if indent is not None and (
        not isinstance(indent, int)
        or isinstance(indent, bool)
        or indent < 0
        or indent > _MAX_SAFE_INTEGER
    ):
        raise TypeError(f"{path}.indent must be null or a non-negative safe integer")
    sort_keys = options.get("sort_keys", True)
    if not isinstance(sort_keys, bool):
        raise TypeError(f"{path}.sort_keys must be a boolean")
    return {"indent": indent, "sort_keys": sort_keys}


def _parquet(options: Mapping[str, object], path: str) -> JsonObject:
    _keys(options, {"compression"}, path)
    compression = options.get("compression", "NONE")
    if compression is None:
        compression = "NONE"
    if not isinstance(compression, str):
        raise TypeError(f"{path}.compression must be a supported string or null")
    compression = compression.upper()
    if compression not in _PARQUET_COMPRESSIONS:
        supported = ", ".join(sorted(_PARQUET_COMPRESSIONS))
        raise ValueError(f"{path}.compression must be one of: {supported}")
    return {"compression": compression}


def _png(options: Mapping[str, object], path: str) -> JsonObject:
    _keys(options, {"scale"}, path)
    scale = options.get("scale", 1)
    if (
        not isinstance(scale, (int, float))
        or isinstance(scale, bool)
        or not math.isfinite(scale)
        or scale <= 0
    ):
        raise TypeError(f"{path}.scale must be a finite positive number")
    if float(scale).is_integer():
        if scale > _MAX_SAFE_INTEGER:
            raise ValueError(f"{path}.scale integer must be within the JavaScript safe range")
        scale = int(scale)
    return {"scale": scale}


_MODULES = {
    "anywidget": "anywidget",
    "arrow": "dataframe",
    "bytes": "bytes",
    "html": "html",
    "json": "json",
    "parquet": "dataframe",
    "png": "vegalite",
    "text": "text",
    "vegalite": "vegalite",
}


def _descriptor(
    name: str,
    version: str,
    normalize_options: _OptionNormalizer = _optionless,
    *,
    extra: str | None = None,
    availability_modules: tuple[str, ...] = (),
) -> _BuiltinExporter:
    return _BuiltinExporter(
        name=name,
        reference=f"marimo_export.exporters.{_MODULES[name]}:{name}",
        version=version,
        normalize_options=normalize_options,
        extra=extra,
        availability_modules=availability_modules,
    )


_BUILTIN_EXPORTERS = (
    _descriptor("json", "json.v1", _json),
    _descriptor("text", "text.v1"),
    _descriptor("html", "html.v1"),
    _descriptor("bytes", "bytes.v1"),
    _descriptor(
        "arrow",
        "dataframe.arrow.v1",
        extra="dataframe",
        availability_modules=("narwhals", "pyarrow"),
    ),
    _descriptor(
        "parquet",
        "dataframe.parquet.v1",
        _parquet,
        extra="dataframe",
        availability_modules=("narwhals", "pyarrow"),
    ),
    _descriptor("vegalite", "vegalite.v1"),
    _descriptor(
        "png",
        "vegalite.png.v1",
        _png,
        extra="png",
        availability_modules=("vl_convert",),
    ),
    _descriptor(
        "anywidget",
        "anywidget.v1",
        extra="anywidget",
        availability_modules=("anywidget", "ipywidgets"),
    ),
)
_BUILTIN_EXPORTERS_BY_NAME = {descriptor.name: descriptor for descriptor in _BUILTIN_EXPORTERS}


def _builtin_exporter(name: str) -> _BuiltinExporter:
    try:
        return _BUILTIN_EXPORTERS_BY_NAME[name]
    except KeyError as error:
        raise ValueError(f"unknown built-in exporter: {name}") from error


def _normalize_options(
    exporter: str,
    options: Mapping[str, object],
    path: str = "options",
) -> JsonObject:
    return _builtin_exporter(exporter).normalize_options(options, path)


def _resolve_builtin(name: str) -> _ResolvedExporter:
    descriptor = _builtin_exporter(name)
    return _ResolvedExporter(
        function=_resolve_import(descriptor.reference).function,
        reference=descriptor.reference,
        version=descriptor.version,
    )


def _resolve_import(reference: str, *, version: str | None = None) -> _ResolvedExporter:
    reference = validate_import_reference(reference, "exporter reference")
    module_name, attribute_path = reference.split(":")
    module = importlib.import_module(module_name)
    value: Any = module
    for part in attribute_path.split("."):
        try:
            value = getattr(value, part)
        except AttributeError as error:
            raise LookupError(f"exporter reference {reference!r} does not exist") from error
    if not callable(value):
        raise TypeError(f"exporter reference {reference!r} is not callable")
    return _ResolvedExporter(
        function=cast(_Exporter, value),
        reference=reference,
        version=_version(version),
    )


def _resolve_variable(
    name: str,
    variables: Mapping[str, object],
    *,
    version: str | None = None,
) -> _ResolvedExporter:
    if not isinstance(name, str) or not name.isidentifier():
        raise ValueError("exporter variable must be a Python identifier")
    try:
        value = variables[name]
    except KeyError as error:
        raise LookupError(f"exporter variable {name!r} does not exist") from error
    if not callable(value):
        raise TypeError(f"exporter variable {name!r} is not callable")
    return _ResolvedExporter(
        function=cast(_Exporter, value),
        reference=f"variable:{name}",
        version=_version(version),
    )


def _version(value: str | None) -> str | None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError("exporter version must be a non-empty string")
    return value


def _keys(options: Mapping[str, object], allowed: set[str], path: str) -> None:
    unknown = set(options) - allowed
    if unknown:
        raise TypeError(f"{path} does not accept: {', '.join(sorted(unknown))}")
