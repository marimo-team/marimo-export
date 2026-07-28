from __future__ import annotations

from typing import Any


def optional(module: str, extra: str) -> Any:
    """Import one dependency owned by an optional Exporter extra."""

    try:
        return __import__(module, fromlist=["*"])
    except ImportError as error:
        package = module.split(".", maxsplit=1)[0]
        raise ImportError(
            f"This Exporter requires {package!r}. Install marimo-export[{extra}]."
        ) from error
