from __future__ import annotations

from marimo_export._marimo.anywidget import create_anywidget_capture
from marimo_export._marimo.blob import BlobAsset
from marimo_export.exporters._anywidget_payload import validate_anywidget_payload

_MEDIA_TYPE = "application/vnd.marimo-export.anywidget.v1+json"


def bundle(widget: object) -> BlobAsset:
    payload = create_anywidget_capture().capture(widget)
    validated = validate_anywidget_payload(payload)
    return BlobAsset(
        data=payload,
        media_type=_MEDIA_TYPE,
        filename=None,
        metadata={
            "models": validated.model_count,
            "root_model_id": validated.root_model_id,
        },
    )
