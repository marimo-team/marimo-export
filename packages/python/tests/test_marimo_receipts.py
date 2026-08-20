from __future__ import annotations

import hashlib

import msgspec
import pytest
from marimo._save.signing import CacheSigner
from marimo._save.stores.dict_store import DictStore
from marimo._save.stubs.lazy_stub import Cache, CacheType, Item, Meta
from marimo_export import OutputSpec
from marimo_export._execution import PlannedOutput
from marimo_export._marimo.capabilities import (
    NativeArrowReturn,
    NativeBlobReturn,
    NativeNumpyReturn,
    NativeScalarReturn,
)
from marimo_export._marimo.compat.cache.attempts import (
    NativeCacheAttempt,
)
from marimo_export._marimo.compat.cache.loader import SequentialLazyLoader
from marimo_export._marimo.compat.cache.receipts import read_cached_return
from marimo_export._marimo.compat.receipts import native_receipt
from marimo_export.descriptors import (
    ArrowDescriptor,
    BlobAssetDescriptor,
    NumpyDescriptor,
    ScalarDescriptor,
)
from marimo_export.errors import OutputError
from marimo_export.exporters import importable


def test_native_receipt_uses_the_bytes_seen_by_the_snapshot() -> None:
    cache_key = "cell_cache/H_expected.jsonl"
    reference = "cell_cache/expected/return.npy"
    payload = b"verified payload"
    manifest = msgspec.json.encode(
        Cache(
            hash="expected",
            cache_type=CacheType.CONTENT_ADDRESSED,
            defs={},
            stateful_refs=[],
            meta=Meta(
                version=1,
                return_value=Item(reference=reference),
                blob_hashes={reference: hashlib.sha256(payload).hexdigest()},
            ),
        )
    )

    class MutatingStore:
        def __init__(self) -> None:
            self.calls: dict[str, int] = {}

        def get(self, key: str) -> bytes | None:
            call = self.calls.get(key, 0)
            self.calls[key] = call + 1
            if key == cache_key:
                return manifest if call == 0 else b'{"hash":"substituted"}'
            if key == reference:
                return payload if call == 0 else b"substituted payload"
            return None

    source = MutatingStore()

    class Loader:
        store = source

        @staticmethod
        def _effective_mode() -> str:
            return "off"

        @staticmethod
        def _resolve_effective_signer(manifest: object, mode: str) -> None:
            del manifest
            assert mode == "off"
            return None

    loader = Loader()
    cached = read_cached_return(
        NativeCacheAttempt(
            loader=loader,
            manifest_key=cache_key,
            expected_hash="expected",
        ),
        output="array",
        value=object(),
        python_type="builtins.object",
    )

    receipt = native_receipt(
        cached=cached,
        output="array",
        planned_output=PlannedOutput(
            name="array",
            source=OutputSpec.value("array").source,
            exporter=None,
        ),
        disposition="hit",
    )

    assert receipt.payload == payload
    assert receipt.descriptor.provenance.to_value() == {"python_type": "builtins.object"}
    assert loader.store is source
    assert source.calls == {cache_key: 1, reference: 1}


def test_scalar_receipt_rejects_live_value_divergence_from_signed_manifest() -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from marimo._save.loaders.lazy import _signable_bytes

    cache_key = "cell_cache/P_expected.jsonl"
    signer = CacheSigner(private_key=Ed25519PrivateKey.generate())
    base = Cache(
        hash="expected",
        cache_type=CacheType.PURE,
        defs={},
        stateful_refs=[],
        meta=Meta(
            version=1,
            return_value=Item(primitive=1),
            signer_public_key=signer.public_key_pem(),
        ),
    )
    manifest = msgspec.structs.replace(
        base,
        meta=msgspec.structs.replace(base.meta, signature=signer.sign(_signable_bytes(base))),
    )
    store = DictStore()
    store.put(cache_key, msgspec.json.encode(manifest))
    loader = SequentialLazyLoader(
        name="scalar-divergence",
        store=store,
        signer=signer,
        mode="verify",
    )

    with pytest.raises(OutputError) as raised:
        read_cached_return(
            NativeCacheAttempt(
                loader=loader,
                manifest_key=cache_key,
                expected_hash="expected",
            ),
            output="answer",
            value=2,
            python_type="builtins.int",
        )

    assert raised.value.code == "cache_receipt_invalid"
    cached = read_cached_return(
        NativeCacheAttempt(
            loader=loader,
            manifest_key=cache_key,
            expected_hash="expected",
        ),
        output="answer",
        value=1,
        python_type="builtins.int",
    )
    assert isinstance(cached, NativeScalarReturn)
    assert cached.value == 1
    assert cached.python_type == "builtins.int"


def test_native_cache_return_variants_map_to_one_descriptor_shape() -> None:
    value_output = PlannedOutput(
        name="value",
        source=OutputSpec.value("value").source,
        exporter=None,
    )
    blob_output = PlannedOutput(
        name="blob",
        source=OutputSpec.value("value").source,
        exporter=importable("example:encode"),
    )

    scalar = native_receipt(
        cached=NativeScalarReturn(python_type="builtins.int", value=1),
        output="value",
        planned_output=value_output,
        disposition="hit",
    )
    numpy = native_receipt(
        cached=NativeNumpyReturn(python_type="numpy.ndarray", payload=b"numpy"),
        output="value",
        planned_output=value_output,
        disposition="hit",
    )
    arrow = native_receipt(
        cached=NativeArrowReturn(python_type="pyarrow.Table", payload=b"arrow"),
        output="value",
        planned_output=value_output,
        disposition="miss",
    )
    blob = native_receipt(
        cached=NativeBlobReturn(
            python_type="marimo_export.outputs.BlobAsset",
            envelope=b"envelope",
            data=b"payload",
            media_type="application/octet-stream",
            filename="value.bin",
            metadata={"kind": "example"},
        ),
        output="blob",
        planned_output=blob_output,
        disposition="miss",
    )

    assert isinstance(scalar.descriptor, ScalarDescriptor)
    assert scalar.payload is None
    assert isinstance(numpy.descriptor, NumpyDescriptor)
    assert numpy.payload == b"numpy"
    assert isinstance(arrow.descriptor, ArrowDescriptor)
    assert arrow.payload == b"arrow"
    assert isinstance(blob.descriptor, BlobAssetDescriptor)
    assert blob.payload == b"envelope"
