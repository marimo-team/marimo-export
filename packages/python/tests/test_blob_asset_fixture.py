from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import msgspec
from marimo._save.stubs import BlobAsset
from marimo_export._json import canonical_bytes

_FIXTURE = (
    Path(__file__).parents[3] / "packages" / "browser" / "tests" / "fixtures" / "blob-asset-v1.json"
)


def test_browser_blob_asset_fixture_matches_live_marimo_encoder() -> None:
    fixture: dict[str, Any] = json.loads(_FIXTURE.read_text())
    blob = BlobAsset(
        data=fixture["data_utf8"].encode("utf-8"),
        media_type=fixture["media_type"],
        filename=fixture["filename"],
        metadata={
            "format_id": fixture["format_id"],
            "metadata_json": canonical_bytes(fixture["metadata"]),
        },
    )

    encoded = msgspec.msgpack.encode(blob)

    assert encoded.hex() == fixture["encoded_hex"]
    assert hashlib.sha256(encoded).hexdigest() == fixture["sha256"]
