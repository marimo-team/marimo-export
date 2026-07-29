from __future__ import annotations

from marimo_export.exporters._spec import ExporterSpec, builtin


def bundle() -> ExporterSpec:
    """Select a self-contained browser bundle for an AnyWidget."""

    return builtin("anywidget.bundle")


__all__ = ["bundle"]
