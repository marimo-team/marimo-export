from __future__ import annotations

import builtins
from typing import Any

from marimo_export import Projection
from marimo_export._builtin_exporters import normalize_builtin_options


def bytes(value: Any, **options: Any) -> Projection:
    normalize_builtin_options("bytes", options, "bytes options")
    if isinstance(value, (bytearray, memoryview)):
        value = value.tobytes() if isinstance(value, memoryview) else builtins.bytes(value)
    if not isinstance(value, builtins.bytes):
        raise TypeError("bytes exporter requires bytes, bytearray, or memoryview")
    return Projection(value, format_id="bytes.v1")
