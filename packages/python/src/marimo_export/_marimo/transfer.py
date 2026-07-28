from __future__ import annotations

import hashlib
import re
import threading
import time
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import urlsplit
from uuid import uuid4

from marimo_export._json import JsonObject
from marimo_export._marimo.compat import (
    NativeReceipt,
    new_transfer_virtual_file,
    transfer_runtime_context,
)
from marimo_export.errors import TransferError
from marimo_export.publication import OutputCodec, ScalarDescriptor

_DEFAULT_TTL_SECONDS = 5 * 60.0
_MAX_TTL_SECONDS = 30 * 60.0
_CLEANUP_RETRY_SECONDS = 1.0
_MAX_ASSETS_PER_TICKET = 4096
_MAX_VIRTUAL_FILE_URL_LENGTH = 2048
_TICKET_ID = re.compile(r"[0-9a-f]{32}")


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
    """One temporary URL for one content-addressed publication asset."""

    codec: OutputCodec
    sha256: str
    size: int
    url: str

    def wire(self) -> JsonObject:
        return {
            "codec": self.codec,
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
    receipts: Iterable[NativeReceipt],
    *,
    ttl_seconds: float = _DEFAULT_TTL_SECONDS,
) -> TransferTicket:
    """Register each unique non-scalar payload as a temporary virtual file."""

    ttl = _validate_ttl(ttl_seconds)
    unique = _payloads(receipts)
    context = cast(_RuntimeContext, transfer_runtime_context())
    if not context.virtual_files_supported:
        raise TransferError("the attached marimo runtime cannot serve virtual files")
    registry = context.virtual_file_registry

    with _LOCK:
        _sweep_expired_locked(_monotonic())
        files: list[_VirtualFile] = []
        assets: list[TransferAsset] = []
        ticket_id: str | None = None
        try:
            for codec, digest, payload in unique:
                virtual_file = _register(
                    registry,
                    context,
                    payload,
                    owned_files=files,
                )
                assets.append(
                    TransferAsset(
                        codec=codec,
                        sha256=digest,
                        size=len(payload),
                        url=virtual_file.url,
                    )
                )

            ticket_id = _allocate_ticket_id_locked()
            deadline = _monotonic() + ttl
            _TICKETS[ticket_id] = _Lease(
                registry=registry,
                files=files,
                deadline=deadline,
            )
            _schedule_locked()
            return TransferTicket(
                id=ticket_id,
                expires_at_ms=int((_wall_time() + ttl) * 1000),
                assets=tuple(assets),
            )
        except Exception as error:
            if ticket_id is not None:
                _TICKETS.pop(ticket_id, None)
            cleanup_errors = _remove_files(registry, files)
            if _TICKETS:
                with suppress(Exception):
                    _schedule_locked()
            if isinstance(error, TransferError) and not cleanup_errors:
                raise
            raise TransferError("failed to register publication assets") from error


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
    """Release expired tickets and return the count fully removed."""

    with _LOCK:
        removed = _sweep_expired_locked(_monotonic())
        _schedule_locked()
        return removed


def _payloads(
    receipts: Iterable[NativeReceipt],
) -> tuple[tuple[OutputCodec, str, bytes], ...]:
    try:
        materialized = tuple(receipts)
    except TypeError as error:
        raise TypeError("receipts must be iterable") from error
    if len(materialized) > _MAX_ASSETS_PER_TICKET:
        raise TransferError(
            f"a transfer ticket may contain at most {_MAX_ASSETS_PER_TICKET} receipts"
        )
    unique: dict[tuple[OutputCodec, str], bytes] = {}
    for receipt in materialized:
        if not isinstance(receipt, NativeReceipt):
            raise TypeError("receipts must contain NativeReceipt values")
        if isinstance(receipt.descriptor, ScalarDescriptor):
            if receipt.payload is not None:
                raise TransferError("a scalar receipt cannot carry transfer bytes")
            continue
        payload = receipt.payload
        if not isinstance(payload, bytes) or not payload:
            raise TransferError("an asset receipt must carry nonempty bytes")
        identity = (receipt.descriptor.codec, receipt.descriptor.asset.sha256)
        if len(payload) != receipt.descriptor.asset.size:
            raise TransferError("an asset receipt size disagrees with its descriptor")
        if hashlib.sha256(payload).hexdigest() != identity[1]:
            raise TransferError("an asset receipt digest disagrees with its descriptor")
        previous = unique.setdefault(identity, payload)
        if previous != payload:
            raise TransferError(f"asset identity {identity!r} has conflicting bytes")
    return tuple((codec, digest, payload) for (codec, digest), payload in sorted(unique.items()))


def _validate_ttl(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("ttl_seconds must be a number")
    ttl = float(value)
    if ttl <= 0 or ttl > _MAX_TTL_SECONDS:
        raise ValueError(f"ttl_seconds must be greater than zero and at most {_MAX_TTL_SECONDS:g}")
    return ttl


def _register(
    registry: _VirtualFileRegistry,
    context: _RuntimeContext,
    payload: bytes,
    *,
    owned_files: list[_VirtualFile],
) -> _VirtualFile:
    for _ in range(100):
        virtual_file = cast(_VirtualFile, new_transfer_virtual_file(payload))
        if not registry.has(virtual_file.filename):
            break
    else:
        raise TransferError("failed to allocate a unique virtual file name")
    _validate_virtual_file(virtual_file, payload)
    owned_files.append(virtual_file)
    registry.add(virtual_file, context)
    if not registry.has(virtual_file.filename):
        raise TransferError("marimo did not register the transfer virtual file")
    return virtual_file


def _validate_virtual_file(virtual_file: _VirtualFile, payload: bytes) -> None:
    filename = virtual_file.filename
    url = virtual_file.url
    if virtual_file.buffer != payload:
        raise TransferError("marimo changed the transfer virtual-file bytes")
    if (
        not isinstance(filename, str)
        or not filename
        or len(filename) > 255
        or not filename.endswith(".bin")
        or any(character in filename for character in ("/", "\\", "\x00"))
    ):
        raise TransferError("marimo produced an invalid transfer filename")
    if not isinstance(url, str) or not url or len(url) > _MAX_VIRTUAL_FILE_URL_LENGTH:
        raise TransferError("marimo produced an invalid transfer URL")
    parsed = urlsplit(url)
    prefix = f"./@file/{len(payload)}-"
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.path != f"{prefix}{filename}"
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


def _allocate_ticket_id_locked() -> str:
    for _ in range(100):
        ticket_id = uuid4().hex
        if ticket_id not in _TICKETS and _TICKET_ID.fullmatch(ticket_id):
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
