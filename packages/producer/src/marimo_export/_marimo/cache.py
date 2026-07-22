from __future__ import annotations

import hashlib
import io
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

from marimo._save.loaders import flush_active_caches
from marimo._save.stores.file import FileStore
from marimo._save.stores.store import Store
from marimo._save.stubs.lazy_stub import BLOB_DESERIALIZERS

from marimo_export._marimo.context import root_context
from marimo_export.errors import IntegrityError, StorageError

_EMPTY_SENTINEL = b"\0"


@contextmanager
def polars_cache_restore_scope() -> Iterator[None]:
    """Restore cached Polars IPC without crossing PyArrow's C data bridge."""

    upstream = BLOB_DESERIALIZERS[".arrow"]

    def restore(data: bytes, type_hint: str | None = None) -> Any:
        if type_hint is None or not type_hint.startswith("polars."):
            return upstream(data, type_hint)

        import polars

        result = polars.read_ipc(io.BytesIO(data))
        if type_hint == "polars.series.series.Series":
            return result.to_series(0)
        return result

    BLOB_DESERIALIZERS[".arrow"] = restore
    try:
        yield
    finally:
        BLOB_DESERIALIZERS[".arrow"] = upstream


def cache_store() -> Store:
    return root_context().cache.store


def flush_caches() -> None:
    flush_active_caches()


def validate_key(key: str) -> str:
    if not key or "\\" in key:
        raise ValueError("cache key must be a non-empty POSIX path")
    path = PurePosixPath(key)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe cache key: {key!r}")
    return path.as_posix()


def put_payload(payload: bytes) -> tuple[str, str, int]:
    digest = hashlib.sha256(payload).hexdigest()
    key = f"marimo-export/payloads/sha256/{digest}"
    _put_immutable(key, payload if payload else _EMPTY_SENTINEL, "payload")
    return key, digest, len(payload)


def read_payload(key: str, digest: str, size: int) -> bytes:
    data = read_key(key)
    payload = b"" if size == 0 and data == _EMPTY_SENTINEL else data
    if len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
        raise IntegrityError(f"projection payload failed integrity verification: {key}")
    return payload


def read_key(key: str) -> bytes:
    safe = validate_key(key)
    data = _read(cache_store(), safe, "cache object")
    if data is None:
        raise FileNotFoundError(f"cache object is missing: {safe}")
    return data


def put_index(key: str, data: bytes) -> None:
    _put_immutable(key, data, "export index")
    flush_caches()


def _put_immutable(key: str, data: bytes, label: str) -> None:
    safe = validate_key(key)
    store = cache_store()
    current = _read(store, safe, label)
    if current != data:
        target = _local_target(store, safe)
        if target is not None:
            _atomic_replace(target, data, label, safe)
        else:
            _put(store, safe, data, label)

    committed = _read(store, safe, label)
    if committed is None:
        raise StorageError(f"failed to read back {label} {safe}")
    if committed != data:
        raise IntegrityError(f"{label} failed commit verification: {safe}")


def _read(store: Store, key: str, label: str) -> bytes | None:
    try:
        return store.get(key)
    except Exception as error:
        raise StorageError(f"failed to read {label} {key}: {error}") from error


def _put(store: Store, key: str, data: bytes, label: str) -> None:
    try:
        store.put(key, data)
    except Exception as error:
        raise StorageError(f"failed to write {label} {key}: {error}") from error


def _local_target(store: Store, key: str) -> Path | None:
    if type(store) is not FileStore:
        return None
    root = store.save_path

    lexical_root = Path(os.path.abspath(root))
    target = lexical_root.joinpath(*PurePosixPath(key).parts)
    resolved_root = lexical_root.resolve()
    resolved_target = target.resolve(strict=False)
    if not resolved_target.is_relative_to(resolved_root):
        raise StorageError(f"local cache path escapes its store root: {key}")
    return target


def _atomic_replace(target: Path, data: bytes, label: str, key: str) -> None:
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    except OSError as error:
        raise StorageError(f"failed to write {label} {key}: {error}") from error
    finally:
        temporary.unlink(missing_ok=True)
