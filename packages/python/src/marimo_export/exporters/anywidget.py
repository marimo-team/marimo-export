from __future__ import annotations

from marimo_export.exporters._spec import ExporterSpec, builtin


def bundle() -> ExporterSpec:
    """Select a browser-loadable AnyWidget model snapshot."""

    return builtin("anywidget.bundle")


__all__ = ["bundle"]
