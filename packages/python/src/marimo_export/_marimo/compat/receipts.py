"""Translate verified native cache returns into export descriptors."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from typing import Any, Literal

from marimo_export._execution.plan import PlannedOutput
from marimo_export._json import canonical_bytes, decode_json, decode_json_object
from marimo_export._marimo.capabilities import (
    NativeArrowReturn,
    NativeBlobReturn,
    NativeCacheReturn,
    NativeNumpyReturn,
    NativeReceipt,
    NativeScalarReturn,
)
from marimo_export._marimo.compat.cache.attempts import CacheAttemptLog, NativeCacheAttempt
from marimo_export._marimo.compat.cache.receipts import read_cached_return
from marimo_export._marimo.compat.inspection import _python_type
from marimo_export._marimo.compat.projections import _NATIVE_ARROW_SCHEMA
from marimo_export.descriptors import (
    ARROW_MEDIA_TYPE,
    JSON_MEDIA_TYPE,
    MARIMO_CELL_MEDIA_TYPE,
    MARIMO_OUTPUT_MEDIA_TYPE,
    ArrowDescriptor,
    AssetRef,
    BlobAssetDescriptor,
    JsonDescriptor,
    MarimoCellDescriptor,
    MarimoOutputDescriptor,
    NumpyDescriptor,
    OutputDescriptor,
    Provenance,
    ScalarDescriptor,
)
from marimo_export.errors import CodecError, OutputError
from marimo_export.outputs import BlobAsset
from marimo_export.spec import CellSource, JsonSource, NativeSource, RenderedOutputSource

_BLOB_ASSET_PYTHON_TYPE = f"{BlobAsset.__module__}.{BlobAsset.__qualname__}"


def collect_output_receipts(
    *,
    child: Any,
    state_name: str,
    outputs: tuple[str, ...],
    planned_outputs: Mapping[str, PlannedOutput],
    output_cell_ids: Mapping[str, Any],
    cache: CacheAttemptLog,
) -> tuple[NativeReceipt, ...]:
    """Collect verified receipts in the export's declared output order."""

    receipts: list[NativeReceipt] = []
    for output in outputs:
        cell_id = output_cell_ids[output]
        disposition = cache.output_cells.get(cell_id)
        if disposition is None:
            raise OutputError(
                f"output {output!r} did not execute through Marimo's cell cache",
                code="cache_receipt_missing",
                details={"state": state_name, "output": output},
            )
        receipts.append(
            output_receipt(
                child.runner,
                cell_id,
                output,
                planned_outputs[output],
                disposition,
                cache.output_attempt(cell_id),
            )
        )
    return tuple(receipts)


def output_receipt(
    child: Any,
    cell_id: Any,
    output: str,
    planned_output: PlannedOutput,
    disposition: Literal["hit", "miss"],
    native_attempt: NativeCacheAttempt,
) -> NativeReceipt:
    value = child.outputs.get(cell_id)
    cached = read_cached_return(
        native_attempt,
        output=output,
        value=value,
        python_type=(
            _BLOB_ASSET_PYTHON_TYPE if planned_output.exporter is not None else _python_type(value)
        ),
    )
    return native_receipt(
        cached=cached,
        output=output,
        planned_output=planned_output,
        disposition=disposition,
    )


def native_receipt(
    *,
    cached: NativeCacheReturn,
    output: str,
    planned_output: PlannedOutput,
    disposition: Literal["hit", "miss"],
) -> NativeReceipt:
    provenance = Provenance(python_type=cached.python_type)
    if isinstance(cached, NativeScalarReturn):
        return NativeReceipt(
            output=output,
            descriptor=ScalarDescriptor(
                value=cached.value,
                provenance=provenance,
            ),
            payload=None,
            disposition=disposition,
        )

    if isinstance(cached, NativeNumpyReturn):
        payload = cached.payload
        asset = AssetRef(sha256=hashlib.sha256(payload).hexdigest(), size=len(payload))
        descriptor: OutputDescriptor = NumpyDescriptor(
            asset=asset,
            provenance=provenance,
        )
    elif isinstance(cached, NativeArrowReturn):
        payload = cached.payload
        asset = AssetRef(sha256=hashlib.sha256(payload).hexdigest(), size=len(payload))
        descriptor = ArrowDescriptor(asset=asset, provenance=provenance)
    elif isinstance(cached, NativeBlobReturn):
        payload = cached.envelope
        asset = AssetRef(sha256=hashlib.sha256(payload).hexdigest(), size=len(payload))
        data = cached.data
        source = planned_output.source
        if (
            isinstance(source, NativeSource)
            and cached.media_type == ARROW_MEDIA_TYPE
            and set(cached.metadata) == {"python_type", "schema"}
            and cached.metadata["schema"] == _NATIVE_ARROW_SCHEMA
            and isinstance(cached.metadata["python_type"], str)
        ):
            payload = data
            asset = AssetRef(sha256=hashlib.sha256(payload).hexdigest(), size=len(payload))
            descriptor = ArrowDescriptor(
                asset=asset,
                provenance=Provenance(python_type=cached.metadata["python_type"]),
            )
            return NativeReceipt(
                output=output,
                descriptor=descriptor,
                payload=payload,
                disposition=disposition,
            )
        if isinstance(source, (JsonSource, NativeSource)) and cached.media_type == JSON_MEDIA_TYPE:
            try:
                value = decode_json(data, f"output {output!r} JSON projection")
            except (TypeError, ValueError) as error:
                raise CodecError(
                    f"output {output!r} has an invalid JSON projection",
                    code="codec_invalid",
                    details={"output": output},
                ) from error
            return NativeReceipt(
                output=output,
                descriptor=JsonDescriptor(value=value, provenance=provenance),
                payload=None,
                disposition=disposition,
            )
        if isinstance(source, JsonSource) and cached.media_type != JSON_MEDIA_TYPE:
            raise CodecError(
                f"output {output!r} has an invalid JSON projection media type",
                code="codec_invalid",
                details={"output": output},
            )
        if isinstance(source, RenderedOutputSource):
            return _snapshot_receipt(
                output=output,
                payload=data,
                provenance=provenance,
                media_type=cached.media_type,
                expected_media_type=MARIMO_OUTPUT_MEDIA_TYPE,
                schema="marimo.output.v1",
                descriptor_type=MarimoOutputDescriptor,
                disposition=disposition,
            )
        if isinstance(source, CellSource):
            return _snapshot_receipt(
                output=output,
                payload=data,
                provenance=provenance,
                media_type=cached.media_type,
                expected_media_type=MARIMO_CELL_MEDIA_TYPE,
                schema="marimo.cell.v1",
                descriptor_type=MarimoCellDescriptor,
                disposition=disposition,
            )
        descriptor = BlobAssetDescriptor(
            asset=asset,
            provenance=provenance,
            media_type=cached.media_type,
            filename=cached.filename,
            metadata=cached.metadata or {},
        )
    else:
        raise TypeError("cached return variant is invalid")
    return NativeReceipt(
        output=output,
        descriptor=descriptor,
        payload=payload,
        disposition=disposition,
    )


def _snapshot_receipt(
    *,
    output: str,
    payload: bytes,
    provenance: Provenance,
    media_type: str,
    expected_media_type: str,
    schema: str,
    descriptor_type: Callable[..., OutputDescriptor],
    disposition: Literal["hit", "miss"],
) -> NativeReceipt:
    if media_type != expected_media_type:
        raise CodecError(
            f"output {output!r} has an invalid snapshot media type",
            code="codec_invalid",
            details={"output": output, "media_type": media_type},
        )
    try:
        document = decode_json_object(payload, f"output {output!r} snapshot")
    except (TypeError, ValueError) as error:
        raise CodecError(
            f"output {output!r} has an invalid snapshot",
            code="codec_invalid",
            details={"output": output},
        ) from error
    if document.get("schema") != schema or canonical_bytes(document) != payload:
        raise CodecError(
            f"output {output!r} has a noncanonical {schema} snapshot",
            code="codec_invalid",
            details={"output": output},
        )
    asset = AssetRef(sha256=hashlib.sha256(payload).hexdigest(), size=len(payload))
    return NativeReceipt(
        output=output,
        descriptor=descriptor_type(asset=asset, provenance=provenance),
        payload=payload,
        disposition=disposition,
    )


__all__ = ["collect_output_receipts", "native_receipt", "output_receipt"]
