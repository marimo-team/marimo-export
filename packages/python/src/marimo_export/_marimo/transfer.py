from __future__ import annotations

import hashlib
import re
import threading
import time
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast
from urllib.parse import urlsplit
from uuid import uuid4

from marimo_export._json import JsonObject
from marimo_export._portable import validate_asset_key
from marimo_export.errors import TransferError

if TYPE_CHECKING:
    from marimo_export._marimo.cache import CacheAssetReceipt


_DEFAULT_TTL_SECONDS = 5 * 60.0
_MAX_TTL_SECONDS = 30 * 60.0
_CLEANUP_RETRY_SECONDS = 1.0
_MAX_ASSETS_PER_TICKET = 4096
_MAX_VIRTUAL_FILE_URL_LENGTH = 2048
_TICKET_ID = re.compile(r"[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class _VirtualFile(Protocol):
    filename: str
    buffer: bytes
    url: str


class _VirtualFileRegistry(Protocol):
    def add(self, virtual_file: _VirtualFile, context: object) -> None: ...

    def has(self, filename: str) -> bool: ...

    def remove(self, virtual_file: _VirtualFile) -> None: ...


class _RuntimeContext(Protocol):
    virtual_files_supported: bool
    virtual_file_registry: _VirtualFileRegistry


@dataclass(frozen=True, slots=True)
class TransferAsset:
    """One temporary download URL for an exact marimo cache asset."""

    key: str
    sha256: str
    size: int
    url: str

    def wire(self) -> JsonObject:
        return {
            "key": self.key,
            "sha256": self.sha256,
            "size": self.size,
            "url": self.url,
        }


@dataclass(frozen=True, slots=True)
class TransferTicket:
    """A bounded lease over temporary marimo virtual files."""

    id: str
    expires_at_ms: int
    assets: tuple[TransferAsset, ...]

    def wire(self) -> JsonObject:
        return {
            "ticket": self.id,
            "expires_at_ms": self.expires_at_ms,
            "assets": [asset.wire() for asset in self.assets],
        }


@dataclass(slots=True)
class _Lease:
    registry: _VirtualFileRegistry
    files: list[_VirtualFile]
    deadline: float


_LOCK = threading.RLock()
_TICKETS: dict[str, _Lease] = {}
_SCHEDULER_TIMER: Any | None = None
_SCHEDULER_TOKEN: object | None = None


def create_ticket(
    receipts: Iterable[CacheAssetReceipt],
    *,
    ttl_seconds: float = _DEFAULT_TTL_SECONDS,
) -> TransferTicket:
    """Register exact cache envelopes as temporary virtual files.

    Repeated references to the same cache key share one virtual file. A cache
    key repeated with different bytes or integrity fields is rejected before
    any resource is registered.
    """

    ttl = _validate_ttl(ttl_seconds)
    unique = _validate_receipts(receipts)
    if not unique:
        raise TransferError("a transfer ticket requires at least one cache asset")

    context = _runtime_context()
    if not context.virtual_files_supported:
        raise TransferError("the attached marimo runtime cannot serve virtual files")
    registry = context.virtual_file_registry

    with _LOCK:
        _sweep_expired_locked(_monotonic())
        files: list[_VirtualFile] = []
        assets: list[TransferAsset] = []
        ticket_id: str | None = None
        try:
            for receipt in unique:
                virtual_file = _register(
                    registry,
                    context,
                    receipt.envelope,
                    owned_files=files,
                )
                asset = receipt.asset
                assets.append(
                    TransferAsset(
                        key=asset.key,
                        sha256=asset.sha256,
                        size=asset.size,
                        url=virtual_file.url,
                    )
                )

            ticket_id = _allocate_ticket_id_locked()
            deadline = _monotonic() + ttl
            expires_at_ms = int((_wall_time() + ttl) * 1000)
            asset_tuple = tuple(assets)
            _TICKETS[ticket_id] = _Lease(
                registry=registry,
                files=files,
                deadline=deadline,
            )
            _schedule_locked()
            return TransferTicket(
                id=ticket_id,
                expires_at_ms=expires_at_ms,
                assets=asset_tuple,
            )
        except Exception as error:
            if ticket_id is not None:
                _TICKETS.pop(ticket_id, None)
            cleanup_errors = _remove_files(registry, files)
            if _TICKETS:
                with suppress(Exception):
                    _schedule_locked()
            message = "failed to register cache assets"
            if cleanup_errors:
                message += f"; cleanup also failed for {len(cleanup_errors)} resource(s)"
            if isinstance(error, TransferError) and not cleanup_errors:
                raise
            raise TransferError(message) from error


def release(ticket_id: str) -> bool:
    """Release a transfer ticket and every virtual file it owns."""

    _validate_ticket_id(ticket_id)
    with _LOCK:
        _sweep_expired_locked(_monotonic())
        lease = _TICKETS.pop(ticket_id, None)
        if lease is None:
            _schedule_locked()
            return False
        errors = _remove_files(lease.registry, lease.files)
        if errors:
            lease.files = [virtual_file for virtual_file, _ in errors]
            lease.deadline = _monotonic() + _CLEANUP_RETRY_SECONDS
            _TICKETS[ticket_id] = lease
            _schedule_locked()
            raise TransferError(
                f"failed to release {len(errors)} virtual file resource(s)"
            ) from errors[0][1]
        _schedule_locked()
        return True


def sweep_expired() -> int:
    """Release expired tickets and return the number fully removed."""

    with _LOCK:
        removed = _sweep_expired_locked(_monotonic())
        _schedule_locked()
        return removed


def _validate_ttl(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("ttl_seconds must be a number")
    ttl = float(value)
    if ttl <= 0 or ttl > _MAX_TTL_SECONDS:
        raise ValueError(f"ttl_seconds must be greater than zero and at most {_MAX_TTL_SECONDS:g}")
    return ttl


def _validate_receipts(
    receipts: Iterable[CacheAssetReceipt],
) -> tuple[CacheAssetReceipt, ...]:
    try:
        materialized = tuple(receipts)
    except TypeError as error:
        raise TypeError("receipts must be iterable") from error
    if len(materialized) > _MAX_ASSETS_PER_TICKET:
        raise TransferError(
            f"a transfer ticket may contain at most {_MAX_ASSETS_PER_TICKET} cache assets"
        )

    unique: dict[str, CacheAssetReceipt] = {}
    for index, receipt in enumerate(materialized):
        try:
            asset = receipt.asset
            envelope = receipt.envelope
        except AttributeError as error:
            raise TypeError(f"receipt {index} is not a cache asset receipt") from error
        _validate_asset(asset.key, asset.sha256, asset.size, envelope, index)
        previous = unique.get(asset.key)
        if previous is None:
            unique[asset.key] = receipt
            continue
        if (
            previous.asset.sha256 != asset.sha256
            or previous.asset.size != asset.size
            or previous.envelope != envelope
        ):
            raise TransferError(f"cache asset key {asset.key!r} has conflicting transfer receipts")
    return tuple(unique.values())


def _validate_asset(
    key: object,
    digest: object,
    size: object,
    envelope: object,
    index: int,
) -> None:
    label = f"receipt {index}"
    try:
        validate_asset_key(key, f"{label} cache asset key")
    except (TypeError, ValueError) as error:
        raise TransferError(f"{label} has an invalid .bin cache asset key") from error
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        raise TransferError(f"{label} has an invalid SHA-256 digest")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise TransferError(f"{label} has an invalid cache asset size")
    if not isinstance(envelope, bytes) or not envelope:
        raise TransferError(f"{label} has an empty or invalid .bin envelope")
    if len(envelope) != size:
        raise TransferError(f"{label} envelope size {len(envelope)} does not match {size}")
    actual = hashlib.sha256(envelope).hexdigest()
    if actual != digest:
        raise TransferError(f"{label} envelope failed SHA-256 verification")


def _register(
    registry: _VirtualFileRegistry,
    context: _RuntimeContext,
    envelope: bytes,
    *,
    owned_files: list[_VirtualFile],
) -> _VirtualFile:
    for _ in range(100):
        virtual_file = _new_virtual_file(envelope)
        if not registry.has(virtual_file.filename):
            break
    else:
        raise TransferError("failed to allocate a unique virtual file name")

    _validate_virtual_file(virtual_file, envelope)
    # Track ownership before calling the storage adapter. Some adapters can
    # raise after allocating the resource.
    owned_files.append(virtual_file)
    registry.add(virtual_file, context)
    if not registry.has(virtual_file.filename):
        raise TransferError("marimo did not register the transfer virtual file")
    return virtual_file


def _validate_virtual_file(virtual_file: _VirtualFile, envelope: bytes) -> None:
    filename = virtual_file.filename
    url = virtual_file.url
    if not isinstance(virtual_file.buffer, bytes) or virtual_file.buffer != envelope:
        raise TransferError("marimo changed the transfer virtual-file bytes")
    if (
        not isinstance(filename, str)
        or not filename
        or len(filename) > 255
        or not filename.endswith(".bin")
        or any(character in filename for character in ("/", "\\", "\x00"))
    ):
        raise TransferError("marimo produced an invalid transfer filename")
    if not isinstance(url, str) or not url:
        raise TransferError("marimo produced an invalid transfer URL")
    if len(url) > _MAX_VIRTUAL_FILE_URL_LENGTH:
        raise TransferError("marimo produced an overlong transfer URL")
    parsed = urlsplit(url)
    expected_prefix = f"./@file/{len(envelope)}-"
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith(expected_prefix)
        or parsed.path != f"{expected_prefix}{filename}"
    ):
        raise TransferError("marimo virtual-file transfer requires a relative @file URL")


def _remove_files(
    registry: _VirtualFileRegistry,
    files: Iterable[_VirtualFile],
) -> list[tuple[_VirtualFile, Exception]]:
    errors: list[tuple[_VirtualFile, Exception]] = []
    for virtual_file in files:
        try:
            registry.remove(virtual_file)
        except Exception as error:
            errors.append((virtual_file, error))
    return errors


def _sweep_expired_locked(now: float) -> int:
    removed = 0
    due = [(ticket_id, lease) for ticket_id, lease in _TICKETS.items() if lease.deadline <= now]
    for ticket_id, lease in due:
        if _TICKETS.get(ticket_id) is not lease:
            continue
        _TICKETS.pop(ticket_id)
        errors = _remove_files(lease.registry, lease.files)
        if errors:
            lease.files = [virtual_file for virtual_file, _ in errors]
            lease.deadline = now + _CLEANUP_RETRY_SECONDS
            _TICKETS[ticket_id] = lease
            continue
        removed += 1
    return removed


def _schedule_locked() -> None:
    global _SCHEDULER_TIMER, _SCHEDULER_TOKEN

    if _SCHEDULER_TIMER is not None:
        _SCHEDULER_TIMER.cancel()
    if not _TICKETS:
        _SCHEDULER_TIMER = None
        _SCHEDULER_TOKEN = None
        return

    token = object()
    deadline = min(lease.deadline for lease in _TICKETS.values())
    timer = _make_timer(max(0.0, deadline - _monotonic()), _run_scheduler, token)
    timer.daemon = True
    _SCHEDULER_TOKEN = token
    _SCHEDULER_TIMER = timer
    timer.start()


def _run_scheduler(token: object) -> None:
    global _SCHEDULER_TIMER, _SCHEDULER_TOKEN

    with _LOCK:
        if token is not _SCHEDULER_TOKEN:
            return
        _SCHEDULER_TIMER = None
        _SCHEDULER_TOKEN = None
        _sweep_expired_locked(_monotonic())
        _schedule_locked()


def _runtime_context() -> _RuntimeContext:
    try:
        from marimo._runtime.context import get_context

        context = get_context()
    except Exception as error:
        raise TransferError(
            "virtual-file transfer requires an active marimo runtime context"
        ) from error
    return cast(_RuntimeContext, context)


def _new_virtual_file(envelope: bytes) -> _VirtualFile:
    from marimo._runtime.virtual_file import VirtualFile, random_filename

    return VirtualFile(filename=random_filename("bin"), buffer=envelope)


def _new_ticket_id() -> str:
    ticket_id = uuid4().hex
    if _TICKET_ID.fullmatch(ticket_id) is None:
        raise RuntimeError("failed to create a bounded transfer ticket identifier")
    return ticket_id


def _allocate_ticket_id_locked() -> str:
    for _ in range(100):
        ticket_id = _new_ticket_id()
        if ticket_id not in _TICKETS:
            return ticket_id
    raise TransferError("failed to allocate a unique transfer ticket identifier")


def _validate_ticket_id(ticket_id: str) -> None:
    if not isinstance(ticket_id, str) or _TICKET_ID.fullmatch(ticket_id) is None:
        raise TransferError("transfer ticket must be 32 lowercase hexadecimal characters")


def _make_timer(
    delay: float,
    callback: Callable[[object], object],
    token: object,
) -> threading.Timer:
    return threading.Timer(delay, callback, args=(token,))


def _monotonic() -> float:
    return time.monotonic()


def _wall_time() -> float:
    return time.time()


__all__ = [
    "TransferAsset",
    "TransferTicket",
    "create_ticket",
    "release",
    "sweep_expired",
]
