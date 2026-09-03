from __future__ import annotations

import pytest
from marimo_export.errors import MarimoExportError
from marimo_export.manifest import (
    MAX_PREPARED_MANIFEST_BYTES,
    PreparedManifestLimitError,
    prepared_manifest_bytes,
)


def test_prepared_manifest_bytes_returns_canonical_portable_json() -> None:
    encoded = prepared_manifest_bytes(
        {
            "schema": "marimo-studio.prepared.v1",
            "inputs": {"choice": "λ", "offset": -0.0},
        }
    )

    assert encoded == (
        b'{"inputs":{"choice":"\xce\xbb","offset":0},"schema":"marimo-studio.prepared.v1"}'
    )


def test_prepared_manifest_bytes_accepts_the_exact_browser_bound() -> None:
    payload = "x" * (MAX_PREPARED_MANIFEST_BYTES - len(b'{"payload":""}'))

    encoded = prepared_manifest_bytes({"payload": payload})

    assert len(encoded) == MAX_PREPARED_MANIFEST_BYTES


def test_prepared_manifest_bytes_reports_the_encoded_size_over_the_bound() -> None:
    payload = "x" * (MAX_PREPARED_MANIFEST_BYTES - len(b'{"payload":""}') + 1)

    with pytest.raises(PreparedManifestLimitError) as captured:
        prepared_manifest_bytes({"payload": payload})

    error = captured.value
    assert isinstance(error, MarimoExportError)
    assert error.code == "prepared_manifest_limit_exceeded"
    assert error.details == {
        "max_bytes": MAX_PREPARED_MANIFEST_BYTES,
        "size_bytes": MAX_PREPARED_MANIFEST_BYTES + 1,
    }


def test_prepared_manifest_bytes_rejects_nonportable_json() -> None:
    with pytest.raises(ValueError, match="JavaScript safe range"):
        prepared_manifest_bytes({"input": 2**53})


def test_manifest_module_exposes_the_focused_public_contract() -> None:
    from marimo_export import manifest

    assert MAX_PREPARED_MANIFEST_BYTES == 262_144
    assert manifest.__all__ == [
        "MAX_PREPARED_MANIFEST_BYTES",
        "PreparedManifestLimitError",
        "prepared_manifest_bytes",
    ]
