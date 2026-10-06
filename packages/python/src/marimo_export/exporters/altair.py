from __future__ import annotations

from marimo_export.exporters._spec import ExporterSpec, builtin


def vegalite() -> ExporterSpec:
    """Select interactive Vega-Lite JSON for an Altair chart."""

    return builtin("altair.vegalite")


__all__ = ["vegalite"]
