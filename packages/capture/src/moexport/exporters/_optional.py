"""Optional dependency loading for exporter families."""

from __future__ import annotations

import importlib
from types import ModuleType

from moexport.exporters._core import MissingOptionalDependency


def import_optional(
    module: str,
    *,
    package: str,
    extra: str,
    purpose: str,
) -> ModuleType:
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise MissingOptionalDependency(
            package=package,
            extra=extra,
            purpose=purpose,
        ) from exc
