from __future__ import annotations

from typing import Any

from marimo_export._builtin_exporters import builtin_exporter


def optional(module: str, exporter: str) -> Any:
    try:
        return __import__(module, fromlist=["*"])
    except ImportError as error:
        package = module.split(".", maxsplit=1)[0]
        extra = builtin_exporter(exporter).extra
        if extra is None:
            raise
        raise ImportError(
            f"This projection requires {package!r}. Install marimo-export[{extra}]."
        ) from error
