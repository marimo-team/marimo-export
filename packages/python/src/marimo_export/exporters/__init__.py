from __future__ import annotations

from collections.abc import Iterable

from marimo_export.errors import SpecError
from marimo_export.exporters._spec import ExporterSpec, builtin, importable


def media(accept: Iterable[str], *, scale: float = 1.0) -> ExporterSpec:
    """Select the first media type in ``accept`` that the value supports.

    The exporter renders figures and charts, or uses the value's display
    methods, as described by ``marimo_export.values.represent()``. ``scale``
    multiplies the pixel density of the PNG images it renders.
    """

    if isinstance(accept, str):
        raise SpecError(
            "invalid exporter: media accept must be a list of media types",
            code="spec_exporter_invalid",
        )
    return builtin("media", {"accept": list(accept), "scale": scale})


__all__ = ["ExporterSpec", "importable", "media"]
