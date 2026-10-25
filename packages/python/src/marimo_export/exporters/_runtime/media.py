from __future__ import annotations

from collections.abc import Iterable, Mapping

from marimo_export.outputs import BlobAsset
from marimo_export.values import Size, represent


def media(
    value: object,
    *,
    accept: Iterable[str],
    scale: float = 1.0,
    size: Mapping[str, float | None] | None = None,
) -> BlobAsset:
    drawn = Size(size.get("width"), size.get("height")) if size is not None else None
    representation = represent(value, accept, scale=scale, size=drawn)
    display = {"width": representation.width, "height": representation.height}
    return BlobAsset(
        data=representation.data,
        media_type=representation.media_type,
        metadata={name: pixels for name, pixels in display.items() if pixels is not None},
    )
