from __future__ import annotations

import importlib.util
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from marimo_export._json import JsonObject

_MAX_SAFE_INTEGER = 2**53 - 1
_PARQUET_COMPRESSIONS = frozenset({"NONE", "SNAPPY", "GZIP", "BROTLI", "LZ4", "ZSTD"})
_EXPORTER_REFS = {
    "anywidget": "marimo_export.projection.exporters.anywidget:anywidget",
    "arrow": "marimo_export.projection.exporters.dataframe:arrow",
    "bytes": "marimo_export.projection.exporters.bytes:bytes",
    "html": "marimo_export.projection.exporters.html:html",
    "json": "marimo_export.projection.exporters.json:json",
    "parquet": "marimo_export.projection.exporters.dataframe:parquet",
    "png": "marimo_export.projection.exporters.vegalite:png",
    "text": "marimo_export.projection.exporters.text:text",
    "vegalite": "marimo_export.projection.exporters.vegalite:vegalite",
}

OptionNormalizer = Callable[[Mapping[str, object], str], JsonObject]


@dataclass(frozen=True)
class BuiltinExporterDescriptor:
    name: str
    ref: str
    cache_version: str
    normalize_options: OptionNormalizer
    extra: str | None = None
    availability_modules: tuple[str, ...] = ()

    def available(self) -> bool:
        return all(importlib.util.find_spec(name) is not None for name in self.availability_modules)


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


def _descriptor(
    name: str,
    cache_version: str,
    normalize_options: OptionNormalizer = _optionless,
    *,
    extra: str | None = None,
    availability_modules: tuple[str, ...] = (),
) -> BuiltinExporterDescriptor:
    return BuiltinExporterDescriptor(
        name=name,
        ref=_EXPORTER_REFS[name],
        cache_version=cache_version,
        normalize_options=normalize_options,
        extra=extra,
        availability_modules=availability_modules,
    )


BUILTIN_EXPORTERS = (
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

BUILTIN_EXPORTERS_BY_NAME = {descriptor.name: descriptor for descriptor in BUILTIN_EXPORTERS}


def builtin_exporter(name: str) -> BuiltinExporterDescriptor:
    try:
        return BUILTIN_EXPORTERS_BY_NAME[name]
    except KeyError as error:
        raise ValueError(f"unknown built-in exporter: {name}") from error


def normalize_builtin_options(
    exporter: str,
    options: Mapping[str, object],
    path: str = "options",
) -> JsonObject:
    return builtin_exporter(exporter).normalize_options(options, path)


def _keys(options: Mapping[str, object], allowed: set[str], path: str) -> None:
    unknown = set(options) - allowed
    if unknown:
        raise TypeError(f"{path} does not accept: {', '.join(sorted(unknown))}")
