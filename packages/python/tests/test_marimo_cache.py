from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import msgspec
import pytest
from marimo._save.cache import CACHE_PREFIX
from marimo._save.stores.file import FileStore
from marimo._save.stubs import CUSTOM_STUBS, CustomStub, register_stub
from marimo._save.stubs.lazy_stub import BlobAsset
from marimo_export._marimo import cache
from marimo_export.errors import IntegrityError
from marimo_export.exporters._registry import _resolve_variable as resolve_variable
from marimo_export.projection import Projection


@dataclass(frozen=True)
class _CustomValue:
    number: int


class _CustomValueStub(CustomStub):
    __slots__ = ("number",)

    def __init__(self, value: _CustomValue) -> None:
        self.number = value.number

    def load(self, glbls: dict[str, Any]) -> _CustomValue:
        del glbls
        return _CustomValue(self.number)

    @staticmethod
    def get_type() -> type:
        return _CustomValue

    def to_bytes(self) -> bytes:
        return self.number.to_bytes(8, "big", signed=True)


def _custom_value_exporter(value: object) -> Projection:
    assert isinstance(value, _CustomValue)
    return Projection(
        str(value.number).encode(),
        format_id="text.v1",
        media_type="text/plain",
    )


def _labeled_custom_value_exporter(value: object) -> Projection:
    assert isinstance(value, _CustomValue)
    return Projection(
        f"number={value.number}".encode(),
        format_id="text.v1",
        media_type="text/plain",
    )


class MemoryStore:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def get(self, key: str) -> bytes | None:
        return self.values.get(key)

    def put(self, key: str, value: bytes) -> bool:
        self.values[key] = value
        return True

    def hit(self, key: str) -> bool:
        return key in self.values

    def clear(self, key: str) -> bool:
        return self.values.pop(key, None) is not None


class FakeLazyLoader:
    def __init__(self, name: str, store: Any) -> None:
        self.name = name
        self.store = store
        self.flushes = 0

    def build_path(self, key: Any) -> str:
        return f"{self.name}/{CACHE_PREFIX[key.cache_type]}{key.hash}.jsonl"

    def flush(self) -> None:
        self.flushes += 1


class FakeCache:
    def __init__(
        self,
        function: Callable[..., Awaitable[BlobAsset]],
        store: Any,
        *,
        reference: str | None = None,
        blob_digest: str | None = None,
        ambiguous_manifest: bool = False,
    ) -> None:
        self.function = function
        self.loader = FakeLazyLoader(_function_name(function), store)
        self.last_hash: str | None = None
        self.hits = 0
        self._reference = reference
        self._blob_digest = blob_digest
        self._ambiguous_manifest = ambiguous_manifest

    async def __call__(self, *args: object) -> BlobAsset:
        assert args[0] == "marimo-export.projection.v1"
        fingerprint = repr((_function_name(self.function), args)).encode()
        self.last_hash = hashlib.sha256(fingerprint).hexdigest()[:24]
        key = f"{self.loader.name}/{self.last_hash}/return.bin"
        cached = self.loader.store.get(key)
        if cached is not None:
            self.hits += 1
            return msgspec.msgpack.decode(cached, type=BlobAsset)

        blob = await self.function(*args)
        envelope = msgspec.msgpack.encode(blob)
        self.loader.store.put(key, envelope)
        returned = self._reference or key
        digest = self._blob_digest or hashlib.sha256(envelope).hexdigest()
        manifest = msgspec.json.encode(
            {
                "hash": self.last_hash,
                "cache_type": "ContentAddressed",
                "defs": {},
                "stateful_refs": [],
                "meta": {
                    "version": 5,
                    "return_value": {"reference": returned},
                    "blob_hashes": {key: digest},
                },
                "ui_defs": [],
            }
        )
        manifest_key = self.loader.build_path(
            SimpleNamespace(
                hash=self.last_hash,
                cache_type="ContentAddressed",
            )
        )
        self.loader.store.put(manifest_key, manifest)
        if self._ambiguous_manifest:
            other_type = next(
                cache_type for cache_type in CACHE_PREFIX if cache_type != "ContentAddressed"
            )
            ambiguous = msgspec.json.encode(
                {
                    "hash": self.last_hash,
                    "cache_type": other_type,
                    "defs": {},
                    "stateful_refs": [],
                    "meta": {
                        "version": 5,
                        "return_value": {"reference": returned},
                        "blob_hashes": {key: digest},
                    },
                    "ui_defs": [],
                }
            )
            ambiguous_key = self.loader.build_path(
                SimpleNamespace(hash=self.last_hash, cache_type=other_type)
            )
            self.loader.store.put(ambiguous_key, ambiguous)
        return blob


class FakePersistentCache:
    def __init__(
        self,
        *,
        reference: str | None = None,
        blob_digest: str | None = None,
        unhashable_primary: bool = False,
        ambiguous_manifest: bool = False,
    ) -> None:
        self.reference = reference
        self.blob_digest = blob_digest
        self.unhashable_primary = unhashable_primary
        self.ambiguous_manifest = ambiguous_manifest

    def __call__(
        self,
        function: Callable[..., Awaitable[BlobAsset]],
        *,
        method: str,
        pin_modules: bool,
        store: Any,
    ) -> FakeCache:
        assert method == "lazy"
        assert pin_modules is True
        if self.unhashable_primary and _function_name(function) == "_project_value":
            return UnhashableCache(function, store)
        return FakeCache(
            function,
            store,
            reference=self.reference,
            blob_digest=self.blob_digest,
            ambiguous_manifest=self.ambiguous_manifest,
        )


class UnhashableCache(FakeCache):
    async def __call__(self, *args: object) -> BlobAsset:
        del args
        raise TypeError(
            "Content addressed hash could not be utilized. "
            "The unhashable arguments/ references are: value"
        )


def _function_name(function: Callable[..., object]) -> str:
    name = getattr(function, "__name__", None)
    assert isinstance(name, str)
    return name


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    root = MemoryStore()
    monkeypatch.setattr(
        cache, "get_context", lambda: SimpleNamespace(cache=SimpleNamespace(store=root))
    )
    monkeypatch.setattr(cache, "LazyLoader", FakeLazyLoader)
    return root


def test_projection_is_persisted_as_verified_blob_asset(
    monkeypatch: pytest.MonkeyPatch,
    store: MemoryStore,
) -> None:
    calls = 0

    async def exporter(value: object, *, prefix: str) -> Projection:
        nonlocal calls
        calls += 1
        return Projection(
            f"{prefix}{value}".encode(),
            format_id="report.text.v1",
            media_type="text/plain; charset=utf-8",
            filename="report.txt",
            metadata={"rows": 3},
        )

    monkeypatch.setattr(cache.marimo, "persistent_cache", FakePersistentCache())
    resolved = resolve_variable("exporter", {"exporter": exporter}, version="exporter.v1")

    receipt = asyncio.run(cache.project_and_cache(7, resolved, {"prefix": "value="}))

    assert calls == 1
    assert receipt.disposition == "miss"
    assert receipt.blob == BlobAsset(
        data=b"value=7",
        media_type="text/plain; charset=utf-8",
        filename="report.txt",
        metadata={
            "format_id": "report.text.v1",
            "metadata_json": b'{"rows":3}',
        },
    )
    assert receipt.envelope == store.values[receipt.asset.key]
    assert receipt.asset.sha256 == hashlib.sha256(receipt.envelope).hexdigest()
    assert receipt.asset.size == len(receipt.envelope)
    assert receipt.asset.key.endswith("/return.bin")


def test_warm_projection_restores_blob_without_calling_exporter(
    monkeypatch: pytest.MonkeyPatch,
    store: MemoryStore,
) -> None:
    calls = 0

    def exporter(value: object) -> Projection:
        nonlocal calls
        calls += 1
        return Projection(str(value).encode(), format_id="text.v1", media_type="text/plain")

    monkeypatch.setattr(cache.marimo, "persistent_cache", FakePersistentCache())
    resolved = resolve_variable("exporter", {"exporter": exporter}, version="exporter.v1")

    cold = asyncio.run(cache.project_and_cache(7, resolved, {}))
    warm = asyncio.run(cache.project_and_cache(7, resolved, {}))

    assert calls == 1
    assert cold.disposition == "miss"
    assert warm.disposition == "hit"
    assert warm.asset == cold.asset
    assert warm.envelope == cold.envelope


def test_unhashable_source_runs_exporter_live_then_persists_bytes(
    monkeypatch: pytest.MonkeyPatch,
    store: MemoryStore,
) -> None:
    calls = 0

    def exporter(value: object) -> Projection:
        nonlocal calls
        calls += 1
        return Projection(
            b"portable",
            format_id="bytes.v1",
            media_type="application/octet-stream",
        )

    monkeypatch.setattr(
        cache.marimo,
        "persistent_cache",
        FakePersistentCache(unhashable_primary=True),
    )
    resolved = resolve_variable("exporter", {"exporter": exporter})

    receipt = asyncio.run(cache.project_and_cache(object(), resolved, {}))

    assert calls == 1
    assert receipt.disposition == "skipped"
    assert receipt.blob.data == b"portable"
    assert receipt.asset.key.startswith("_persist_blob_asset/")
    assert receipt.envelope == store.values[receipt.asset.key]


def test_projection_rejects_manifest_with_wrong_return_reference(
    monkeypatch: pytest.MonkeyPatch,
    store: MemoryStore,
) -> None:
    def exporter(value: object) -> Projection:
        return Projection(b"value", format_id="bytes.v1", media_type="application/octet-stream")

    monkeypatch.setattr(
        cache.marimo,
        "persistent_cache",
        FakePersistentCache(reference="wrong/return.bin"),
    )
    resolved = resolve_variable("exporter", {"exporter": exporter})

    with pytest.raises(IntegrityError, match="manifest is missing or invalid"):
        asyncio.run(cache.project_and_cache("value", resolved, {}))


def test_projection_rejects_manifest_blob_digest_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    store: MemoryStore,
) -> None:
    def exporter(value: object) -> Projection:
        return Projection(b"value", format_id="bytes.v1", media_type="application/octet-stream")

    monkeypatch.setattr(
        cache.marimo,
        "persistent_cache",
        FakePersistentCache(blob_digest="0" * 64),
    )
    resolved = resolve_variable("exporter", {"exporter": exporter})

    with pytest.raises(IntegrityError, match="failed integrity"):
        asyncio.run(cache.project_and_cache("value", resolved, {}))


def test_projection_rejects_ambiguous_matching_manifests(
    monkeypatch: pytest.MonkeyPatch,
    store: MemoryStore,
) -> None:
    def exporter(value: object) -> Projection:
        return Projection(
            b"value",
            format_id="bytes.v1",
            media_type="application/octet-stream",
        )

    monkeypatch.setattr(
        cache.marimo,
        "persistent_cache",
        FakePersistentCache(ambiguous_manifest=True),
    )
    resolved = resolve_variable("exporter", {"exporter": exporter})

    with pytest.raises(IntegrityError, match="manifest is ambiguous"):
        asyncio.run(cache.project_and_cache("value", resolved, {}))


def test_registered_custom_stub_uses_cacheable_projector_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = FileStore(save_path=str(tmp_path))
    monkeypatch.setattr(
        cache,
        "get_context",
        lambda: SimpleNamespace(cache=SimpleNamespace(store=root)),
    )
    previous = CUSTOM_STUBS.get(_CustomValue)
    register_stub(_CustomValue, _CustomValueStub)
    try:
        resolved = resolve_variable(
            "exporter",
            {"exporter": _custom_value_exporter},
            version="custom.v1",
        )
        cold = asyncio.run(cache.project_and_cache(_CustomValue(7), resolved, {}))
        warm = asyncio.run(cache.project_and_cache(_CustomValue(7), resolved, {}))
        changed = asyncio.run(cache.project_and_cache(_CustomValue(8), resolved, {}))
        changed_version = asyncio.run(
            cache.project_and_cache(
                _CustomValue(7),
                resolve_variable(
                    "exporter",
                    {"exporter": _custom_value_exporter},
                    version="custom.v2",
                ),
                {},
            )
        )
        changed_callable = asyncio.run(
            cache.project_and_cache(
                _CustomValue(7),
                resolve_variable(
                    "exporter",
                    {"exporter": _labeled_custom_value_exporter},
                    version="custom.v1",
                ),
                {},
            )
        )
    finally:
        if previous is None:
            CUSTOM_STUBS.pop(_CustomValue, None)
        else:
            CUSTOM_STUBS[_CustomValue] = previous

    assert cold.disposition == "miss"
    assert warm.disposition == "hit"
    assert changed.disposition == "miss"
    assert changed_version.disposition == "miss"
    assert changed_callable.disposition == "miss"
    assert cold.asset == warm.asset
    assert changed.asset.key != cold.asset.key
    assert changed_version.asset.key != cold.asset.key
    assert changed_callable.asset.key != cold.asset.key
    assert changed.blob.data == b"8"
    assert changed_callable.blob.data == b"number=7"
    assert cold.asset.key.startswith("_project_value/")
