from __future__ import annotations

from marimo_export.exporters._spec import ExporterSpec, builtin


def vegalite() -> ExporterSpec:
    """Select interactive Vega-Lite JSON for an Altair chart."""

    return builtin("altair.vegalite")


def png(*, scale: float = 1.0) -> ExporterSpec:
    """Select a PNG rendering for an Altair chart."""

    return builtin("altair.png", {"scale": scale})


__all__ = ["png", "vegalite"]
