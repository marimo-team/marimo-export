from __future__ import annotations

import builtins
from typing import Any

from marimo_export.exporters._registry import _normalize_options
from marimo_export.projection import Projection


def bytes(value: Any, **options: Any) -> Projection:
    _normalize_options("bytes", options, "bytes options")
    if isinstance(value, (bytearray, memoryview)):
        value = value.tobytes() if isinstance(value, memoryview) else builtins.bytes(value)
    if not isinstance(value, builtins.bytes):
        raise TypeError("bytes exporter requires bytes, bytearray, or memoryview")
    return Projection(value, format_id="bytes.v1", media_type="application/octet-stream")
