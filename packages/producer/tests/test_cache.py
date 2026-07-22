from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from marimo._save.stores.file import FileStore
from marimo._save.stores.store import Store
from marimo_export._marimo import cache
from marimo_export.errors import IntegrityError, StorageError


class MemoryStore:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def get(self, key: str) -> bytes | None:
        return self.values.get(key)

    def put(self, key: str, value: bytes) -> bool:
        self.values[key] = value
        return True


@pytest.mark.parametrize("corrupt", [b"x", b"tampered"])
def test_put_payload_repairs_corrupt_content_addressed_object(
    monkeypatch: pytest.MonkeyPatch, corrupt: bytes
) -> None:
    store = MemoryStore()
    payload = b"portable"
    digest = hashlib.sha256(payload).hexdigest()
    key = f"marimo-export/payloads/sha256/{digest}"
    store.values[key] = corrupt
    monkeypatch.setattr(cache, "cache_store", lambda: store)

    assert cache.put_payload(payload) == (key, digest, len(payload))
    assert cache.read_payload(key, digest, len(payload)) == payload


def test_put_index_repairs_corrupt_object(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore()
    data = b'{"schema":"marimo-export.index.v1"}'
    digest = hashlib.sha256(data).hexdigest()
    key = f"marimo-export/indexes/{digest}.json"
    store.values[key] = b"corrupt"
    monkeypatch.setattr(cache, "cache_store", lambda: store)
    monkeypatch.setattr(cache, "flush_caches", lambda: None)

    cache.put_index(key, data)

    assert cache.read_key(key) == data


def test_empty_payload_uses_nonempty_store_value(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore()
    monkeypatch.setattr(cache, "cache_store", lambda: store)

    key, digest, size = cache.put_payload(b"")

    assert store.values[key] == b"\0"
    assert cache.read_payload(key, digest, size) == b""


def test_commit_fails_when_store_loses_readback(monkeypatch: pytest.MonkeyPatch) -> None:
    class LostWriteStore:
        def get(self, key: str) -> bytes | None:
            return None

        def put(self, key: str, value: bytes) -> bool:
            return True

    monkeypatch.setattr(cache, "cache_store", LostWriteStore)

    with pytest.raises(StorageError, match="failed to read back payload"):
        cache.put_payload(b"portable")


def test_commit_fails_when_readback_has_same_length_corruption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CorruptingStore:
        written = False

        def get(self, key: str) -> bytes | None:
            return b"tampered" if self.written else None

        def put(self, key: str, value: bytes) -> bool:
            self.written = True
            return True

    monkeypatch.setattr(cache, "cache_store", CorruptingStore)

    with pytest.raises(IntegrityError, match="failed commit verification"):
        cache.put_payload(b"portable")


def test_concurrent_file_store_writes_converge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = FileStore(str(tmp_path / "cache"))
    monkeypatch.setattr(cache, "cache_store", lambda: store)
    payload = b"one immutable projection"
    workers = 8
    barrier = Barrier(workers)

    def write() -> tuple[str, str, int]:
        barrier.wait()
        return cache.put_payload(payload)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(lambda _: write(), range(workers)))

    assert len(set(results)) == 1
    key, digest, size = results[0]
    assert cache.read_payload(key, digest, size) == payload
    assert list((tmp_path / "cache").rglob("*.tmp")) == []


def test_custom_store_with_save_path_uses_store_put(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class SavePathStore(Store):
        def __init__(self) -> None:
            self.save_path = tmp_path / "cache"
            self.values: dict[str, bytes] = {}
            self.put_calls = 0

        def get(self, key: str) -> bytes | None:
            return self.values.get(key)

        def put(self, key: str, value: bytes) -> bool:
            self.put_calls += 1
            self.values[key] = value
            return True

        def hit(self, key: str) -> bool:
            return key in self.values

    store = SavePathStore()
    monkeypatch.setattr(cache, "cache_store", lambda: store)

    key, digest, size = cache.put_payload(b"portable")

    assert store.put_calls == 1
    assert store.values[key] == b"portable"
    assert cache.read_payload(key, digest, size) == b"portable"
    assert not store.save_path.exists()


def test_file_store_subclass_uses_overridden_put(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class OverridingFileStore(FileStore):
        def __init__(self, save_path: str) -> None:
            super().__init__(save_path)
            self.put_calls = 0

        def put(self, key: str, value: bytes) -> bool:
            self.put_calls += 1
            return super().put(key, value)

    store = OverridingFileStore(str(tmp_path / "cache"))
    monkeypatch.setattr(cache, "cache_store", lambda: store)

    key, digest, size = cache.put_payload(b"portable")

    assert store.put_calls == 1
    assert cache.read_payload(key, digest, size) == b"portable"


def test_write_rejects_parent_traversal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = FileStore(str(tmp_path / "cache"))
    monkeypatch.setattr(cache, "cache_store", lambda: store)

    with pytest.raises(ValueError, match="unsafe cache key"):
        cache.put_index("../outside.json", b"index")

    assert not (tmp_path / "outside.json").exists()


def test_write_rejects_symlink_escape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "cache"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "marimo-export").symlink_to(outside, target_is_directory=True)
    store = FileStore(str(root))
    monkeypatch.setattr(cache, "cache_store", lambda: store)

    with pytest.raises(StorageError, match="escapes its store root"):
        cache.put_payload(b"portable")

    assert list(outside.rglob("*")) == []
