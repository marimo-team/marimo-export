from __future__ import annotations

from collections.abc import Iterable

from marimo_export.errors import OutputError
from marimo_export.outputs import BlobAsset
from marimo_export.values import RepresentationError, represent


def media(value: object, *, accept: Iterable[str], scale: float = 1.0) -> BlobAsset:
    try:
        representation = represent(value, accept, scale=scale)
    except RepresentationError as error:
        raise OutputError(str(error), code="output_execution_failed") from error
    size = {"width": representation.width, "height": representation.height}
    return BlobAsset(
        data=representation.data,
        media_type=representation.media_type,
        metadata={name: pixels for name, pixels in size.items() if pixels is not None},
    )
