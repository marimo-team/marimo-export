from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import cast

from marimo_export.errors import OutputError
from marimo_export.outputs import BlobAsset
from marimo_export.values import RepresentationError, Size, represent


def media(
    value: object,
    *,
    accept: Iterable[str],
    scale: float = 1.0,
    size: Mapping[str, float | None] | None = None,
) -> BlobAsset:
    drawn = Size(cast(float, size["width"]), size.get("height")) if size is not None else None
    try:
        representation = represent(value, accept, scale=scale, size=drawn)
    except RepresentationError as error:
        raise OutputError(str(error), code="output_execution_failed") from error
    display = {"width": representation.width, "height": representation.height}
    return BlobAsset(
        data=representation.data,
        media_type=representation.media_type,
        metadata={name: pixels for name, pixels in display.items() if pixels is not None},
    )
