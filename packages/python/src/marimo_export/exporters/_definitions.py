from __future__ import annotations

import keyword
import math
from collections.abc import Mapping
from dataclasses import dataclass

from marimo_export._json import JsonObject, JsonValue, portable_json_object


@dataclass(frozen=True, slots=True)
class ExporterDefinition:
    module: str
    symbol: str


_BUILTINS = {
    "altair.png": ExporterDefinition(
        module="marimo_export.exporters._runtime.altair",
        symbol="png",
    ),
    "altair.vegalite": ExporterDefinition(
        module="marimo_export.exporters._runtime.altair",
        symbol="vegalite",
    ),
    "anywidget.bundle": ExporterDefinition(
        module="marimo_export.exporters._runtime.anywidget",
        symbol="bundle",
    ),
    "blob.html": ExporterDefinition(
        module="marimo_export.exporters._runtime.blob",
        symbol="html",
    ),
    "blob.json": ExporterDefinition(
        module="marimo_export.exporters._runtime.blob",
        symbol="json",
    ),
    "blob.text": ExporterDefinition(
        module="marimo_export.exporters._runtime.blob",
        symbol="text",
    ),
    "parquet.table": ExporterDefinition(
        module="marimo_export.exporters._runtime.parquet",
        symbol="table",
    ),
}
_COMPRESSIONS = frozenset({"snappy", "none", "gzip", "brotli", "lz4", "zstd"})


def normalize_exporter(name: object, options: object) -> tuple[str, JsonObject]:
    if not isinstance(name, str) or not name:
        raise TypeError("exporter name must be a non-empty string")
    parsed = portable_json_object(options, f"exporter {name!r} options")
    if name in _BUILTINS:
        return name, _normalize_builtin_options(name, parsed)
    _parse_import_reference(name)
    for option in parsed:
        if not option.isidentifier() or keyword.iskeyword(option):
            raise ValueError(
                f"custom exporter option {option!r} must be a non-keyword Python identifier"
            )
    return name, parsed


def runtime_reference(name: str) -> ExporterDefinition:
    definition = _BUILTINS.get(name)
    if definition is not None:
        return definition
    module, symbol = _parse_import_reference(name)
    return ExporterDefinition(module=module, symbol=symbol)


def _normalize_builtin_options(name: str, options: JsonObject) -> JsonObject:
    if name in {"altair.vegalite", "anywidget.bundle"}:
        _exact_options(name, options, set())
        return {}
    if name == "altair.png":
        _exact_options(name, options, {"scale"})
        scale = options.get("scale", 1.0)
        if (
            isinstance(scale, bool)
            or not isinstance(scale, (int, float))
            or not math.isfinite(scale)
            or scale <= 0
        ):
            raise TypeError("altair.png option 'scale' must be a positive finite number")
        return {"scale": scale}
    if name == "parquet.table":
        _exact_options(name, options, {"compression", "filename"})
        compression = options.get("compression", "snappy")
        if not isinstance(compression, str) or compression not in _COMPRESSIONS:
            raise ValueError(
                "parquet.table option 'compression' must be one of: "
                "brotli, gzip, lz4, none, snappy, zstd"
            )
        result: JsonObject = {"compression": compression}
        if "filename" in options:
            result["filename"] = _optional_filename(options["filename"], name)
        return result
    if name == "blob.json":
        return _blob_options(
            name,
            options,
            default_media_type="application/json",
            accepts_media_type=True,
        )
    if name == "blob.text":
        return _blob_options(
            name,
            options,
            default_media_type="text/plain; charset=utf-8",
            accepts_media_type=True,
        )
    if name == "blob.html":
        return _blob_options(
            name,
            options,
            default_media_type=None,
            accepts_media_type=False,
        )
    raise AssertionError(f"unhandled built-in exporter {name!r}")


def _blob_options(
    name: str,
    options: JsonObject,
    *,
    default_media_type: str | None,
    accepts_media_type: bool,
) -> JsonObject:
    accepted = {"filename", "metadata"}
    if accepts_media_type:
        accepted.add("media_type")
    _exact_options(name, options, accepted)
    result: JsonObject = {}
    if default_media_type is not None:
        media_type = options.get("media_type", default_media_type)
        if not isinstance(media_type, str) or not media_type.strip():
            raise TypeError(f"{name} option 'media_type' must be a non-empty string")
        result["media_type"] = media_type
    if "filename" in options:
        result["filename"] = _optional_filename(options["filename"], name)
    if "metadata" in options:
        metadata = options["metadata"]
        if not isinstance(metadata, dict):
            raise TypeError(f"{name} option 'metadata' must be an object")
        result["metadata"] = metadata
    return result


def _optional_filename(value: JsonValue, name: str) -> JsonValue:
    if value is not None and (not isinstance(value, str) or not value):
        raise TypeError(f"{name} option 'filename' must be a non-empty string or null")
    return value


def _exact_options(name: str, options: Mapping[str, JsonValue], accepted: set[str]) -> None:
    unexpected = sorted(set(options) - accepted)
    if unexpected:
        raise ValueError(f"{name} does not accept option {unexpected[0]!r}")


def _parse_import_reference(value: str) -> tuple[str, str]:
    if value.count(":") != 1:
        raise ValueError(f"unknown exporter {value!r}; custom exporters use 'module:function'")
    module, symbol = value.split(":", maxsplit=1)
    parts = module.split(".")
    if (
        not module
        or any(not part.isidentifier() or keyword.iskeyword(part) for part in parts)
        or not symbol.isidentifier()
        or keyword.iskeyword(symbol)
    ):
        raise ValueError(f"custom exporter {value!r} must name an importable top-level function")
    return module, symbol


__all__ = ["ExporterDefinition", "normalize_exporter", "runtime_reference"]
