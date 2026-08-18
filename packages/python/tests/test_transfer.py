from __future__ import annotations

import hashlib

import pytest
from marimo_export._marimo.capabilities import NativeReceipt
from marimo_export._marimo.transfer import _MAX_ASSETS_PER_TICKET, _payloads
from marimo_export.errors import IntegrityError
from marimo_export.export import AssetRef, NumpyDescriptor, Provenance, ScalarDescriptor


def _provenance() -> Provenance:
    return Provenance(
        cache_key="cell_cache/value.json",
        return_reference=None,
        python_type="builtins.int",
    )


def test_transfer_asset_limit_applies_after_scalar_filtering_and_deduplication() -> None:
    scalar = NativeReceipt(
        output="value",
        descriptor=ScalarDescriptor(value=1, provenance=_provenance()),
        payload=None,
        disposition="hit",
    )
    assert _payloads([scalar] * (_MAX_ASSETS_PER_TICKET + 1)) == ()

    payload = b"shared"
    digest = hashlib.sha256(payload).hexdigest()
    shared = NativeReceipt(
        output="array",
        descriptor=NumpyDescriptor(
            asset=AssetRef(digest, len(payload)),
            provenance=Provenance(
                cache_key="cell_cache/array.json",
                return_reference="cell_cache/array/return.npy",
                python_type="numpy.ndarray",
            ),
        ),
        payload=payload,
        disposition="hit",
    )
    assert len(_payloads([shared] * (_MAX_ASSETS_PER_TICKET + 1))) == 1

    unique = []
    for position in range(_MAX_ASSETS_PER_TICKET + 1):
        value = position.to_bytes(4, "big")
        value_digest = hashlib.sha256(value).hexdigest()
        unique.append(
            NativeReceipt(
                output=f"array_{position}",
                descriptor=NumpyDescriptor(
                    asset=AssetRef(value_digest, len(value)),
                    provenance=Provenance(
                        cache_key=f"cell_cache/array_{position}.json",
                        return_reference=f"cell_cache/array_{position}/return.npy",
                        python_type="numpy.ndarray",
                    ),
                ),
                payload=value,
                disposition="hit",
            )
        )
    with pytest.raises(IntegrityError, match="at most 4096 assets"):
        _payloads(unique)
