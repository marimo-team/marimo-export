from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any, cast

import marimo_export.delivery as delivery
import pytest
from marimo_export.delivery import DeliveryResult, StagedDelivery, stage
from marimo_export.observations import (
    ObservationLedger,
    ObservationPersistenceError,
    ObservationRejectedError,
    ObservedInputs,
    install_observation_ledger,
)
from marimo_export.outputs import BlobAsset
from marimo_export.prepared import PreparedAsset, PreparedExport
from marimo_export.sessions import Client, Session, connect


def test_focused_public_modules_expose_session_and_output_capabilities() -> None:
    assert BlobAsset.__name__ == "BlobAsset"
    assert BlobAsset.__module__ == "marimo_export.outputs"
    assert Client.__name__ == "Client"
    assert Session.__name__ == "Session"
    assert PreparedAsset.__module__ == "marimo_export.prepared"
    assert PreparedExport.__module__ == "marimo_export.prepared"
    assert DeliveryResult.__module__ == "marimo_export.delivery"
    assert StagedDelivery.__module__ == "marimo_export.delivery"
    with pytest.raises(TypeError, match="borrowed"):
        PreparedAsset()
    with pytest.raises(TypeError, match="returned"):
        PreparedExport()
    with pytest.raises(TypeError, match="returned"):
        StagedDelivery()
    assert callable(connect)
    assert callable(stage)
    assert delivery.__all__ == ["DeliveryResult", "StagedDelivery", "stage"]


def test_observation_module_exposes_records_and_lifecycle_capabilities() -> None:
    assert ObservedInputs.__module__ == "marimo_export.observations"
    assert ObservationPersistenceError.__module__ == "marimo_export.observations"
    assert ObservationRejectedError.__module__ == "marimo_export.observations"
    assert ObservationLedger.__name__ == "ObservationLedger"
    assert callable(install_observation_ledger)


def test_blob_asset_owns_immutable_portable_output_data() -> None:
    metadata = {"nested": {"labels": ["one", "two"]}, "negative_zero": -0.0}
    asset = BlobAsset(
        data=b"payload",
        media_type="application/vnd.example.payload+json",
        filename="payload.json",
        metadata=metadata,
    )
    metadata["nested"] = {"labels": ["changed"]}

    assert asset.data == b"payload"
    assert asset.media_type == "application/vnd.example.payload+json"
    assert asset.filename == "payload.json"
    assert asset.metadata == {
        "negative_zero": 0,
        "nested": {"labels": ("one", "two")},
    }
    with pytest.raises(FrozenInstanceError):
        cast(Any, asset).data = b"changed"
    with pytest.raises(TypeError):
        cast(Any, asset.metadata)["other"] = 1
    with pytest.raises(TypeError):
        cast(Any, asset.metadata["nested"])["other"] = 1


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ({"data": bytearray(b"payload")}, TypeError),
        ({"data": b"payload", "media_type": "invalid"}, ValueError),
        ({"data": b"payload", "filename": "../payload.bin"}, ValueError),
        ({"data": b"payload", "metadata": {"binary": b"value"}}, TypeError),
        ({"data": b"payload", "metadata": {"integer": 2**53}}, ValueError),
    ],
)
def test_blob_asset_rejects_nonportable_fields(
    arguments: dict[str, object],
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        BlobAsset(**cast(Any, arguments))
