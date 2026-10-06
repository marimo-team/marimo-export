from __future__ import annotations

from collections.abc import Iterable

from marimo_export.errors import SpecError
from marimo_export.exporters._spec import ExporterSpec, builtin, importable
from marimo_export.values import normalize_accept


def media(accept: Iterable[str], *, scale: float = 1.0) -> ExporterSpec:
    """Select the first media type in ``accept`` that the value supports.

    The exporter renders figures and charts, or uses the value's display
    methods, as described by ``marimo_export.values.represent()``. ``scale``
    multiplies the pixel density of the PNG images it renders.
    """

    if isinstance(accept, str) or not isinstance(accept, Iterable):
        raise SpecError(
            "invalid exporter: media accept must be a list of media types",
            code="spec_exporter_invalid",
        )
    try:
        accepted = normalize_accept(accept)
    except (TypeError, ValueError) as error:
        raise SpecError(f"invalid exporter: {error}", code="spec_exporter_invalid") from error
    return builtin("media", {"accept": list(accepted), "scale": scale})


__all__ = ["ExporterSpec", "importable", "media"]
