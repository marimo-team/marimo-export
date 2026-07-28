from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import pytest
from marimo_export._marimo import transfer
from marimo_export.errors import TransferError

if TYPE_CHECKING:
    from marimo_export._marimo.cache import CacheAssetReceipt


@dataclass(frozen=True)
class _AssetRef:
    key: str
    sha256: str
    size: int


@dataclass(frozen=True)
class _Receipt:
    asset: _AssetRef
    envelope: bytes
    cache_hit: bool = False


@dataclass
class _VirtualFile:
    filename: str
    buffer: bytes
    url: str


class _Registry:
    def __init__(self) -> None:
        self.files: dict[str, _VirtualFile] = {}
        self.added: list[_VirtualFile] = []
        self.removed: list[_VirtualFile] = []
        self.fail_add_at: int | None = None
        self.fail_remove: set[str] = set()

    def add(self, virtual_file: _VirtualFile, context: object) -> None:
        del context
        self.added.append(virtual_file)
        self.files[virtual_file.filename] = virtual_file
        if self.fail_add_at == len(self.added):
            raise RuntimeError("add failed")

    def has(self, filename: str) -> bool:
        return filename in self.files

    def remove(self, virtual_file: _VirtualFile) -> None:
        self.removed.append(virtual_file)
        if virtual_file.filename in self.fail_remove:
            raise RuntimeError("remove failed")
        self.files.pop(virtual_file.filename, None)


@dataclass
class _Context:
    virtual_file_registry: _Registry
    virtual_files_supported: bool = True


class _Clock:
    def __init__(self) -> None:
        self.monotonic = 100.0
        self.wall = 1_750_000_000.0

    def advance(self, seconds: float) -> None:
        self.monotonic += seconds
        self.wall += seconds


class _Timer:
    def __init__(self, delay: float, callback: Callable[[object], object], token: object) -> None:
        self.delay = delay
        self.callback = callback
        self.token = token
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        self.callback(self.token)


@pytest.fixture(autouse=True)
def isolated_transfer_state(monkeypatch: pytest.MonkeyPatch):
    clock = _Clock()
    timers: list[_Timer] = []
    filenames = iter(f"vf-{index}.bin" for index in range(100))
    ticket_ids = iter(f"{index:032x}" for index in range(1, 100))

    def make_file(envelope: bytes) -> _VirtualFile:
        filename = next(filenames)
        return _VirtualFile(
            filename=filename,
            buffer=envelope,
            url=f"./@file/{len(envelope)}-{filename}",
        )

    def make_timer(delay: float, callback: Callable[[object], object], token: object) -> _Timer:
        timer = _Timer(delay, callback, token)
        timers.append(timer)
        return timer

    monkeypatch.setattr(transfer, "_TICKETS", {})
    monkeypatch.setattr(transfer, "_SCHEDULER_TIMER", None)
    monkeypatch.setattr(transfer, "_SCHEDULER_TOKEN", None)
    monkeypatch.setattr(transfer, "_monotonic", lambda: clock.monotonic)
    monkeypatch.setattr(transfer, "_wall_time", lambda: clock.wall)
    monkeypatch.setattr(transfer, "_new_virtual_file", make_file)
    monkeypatch.setattr(transfer, "_new_ticket_id", lambda: next(ticket_ids))
    monkeypatch.setattr(transfer, "_make_timer", make_timer)
    yield clock, timers
    for timer in timers:
        timer.cancel()


def _receipt(key: str, envelope: bytes = b"envelope") -> CacheAssetReceipt:
    return cast(
        "CacheAssetReceipt",
        _Receipt(
            asset=_AssetRef(
                key=key,
                sha256=hashlib.sha256(envelope).hexdigest(),
                size=len(envelope),
            ),
            envelope=envelope,
        ),
    )


def _install_context(monkeypatch: pytest.MonkeyPatch, context: _Context) -> None:
    monkeypatch.setattr(transfer, "_runtime_context", lambda: context)


def test_explicit_release_removes_exact_registered_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _Registry()
    _install_context(monkeypatch, _Context(registry))

    ticket = transfer.create_ticket(
        [_receipt("cache/a/return.bin"), _receipt("cache/b/return.bin", b"second")]
    )

    assert [asset.key for asset in ticket.assets] == [
        "cache/a/return.bin",
        "cache/b/return.bin",
    ]
    assert ticket.wire() == {
        "ticket": ticket.id,
        "expires_at_ms": ticket.expires_at_ms,
        "assets": [asset.wire() for asset in ticket.assets],
    }
    assert transfer.release(ticket.id) is True
    assert registry.removed == registry.added
    assert all(
        removed is added for removed, added in zip(registry.removed, registry.added, strict=True)
    )
    assert registry.files == {}
    assert transfer.release(ticket.id) is False


def test_timer_releases_expired_ticket_without_another_operation(
    monkeypatch: pytest.MonkeyPatch,
    isolated_transfer_state: tuple[_Clock, list[_Timer]],
) -> None:
    clock, timers = isolated_transfer_state
    registry = _Registry()
    _install_context(monkeypatch, _Context(registry))
    ticket = transfer.create_ticket([_receipt("cache/a/return.bin")], ttl_seconds=2)

    timer = timers[-1]
    assert timer.started is True
    assert timer.delay == pytest.approx(2)

    clock.advance(2)
    timer.fire()

    assert registry.files == {}
    assert registry.removed == registry.added
    assert transfer.release(ticket.id) is False


def test_registration_failure_cleans_every_allocated_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _Registry()
    registry.fail_add_at = 2
    _install_context(monkeypatch, _Context(registry))

    with pytest.raises(TransferError, match="failed to register cache assets"):
        transfer.create_ticket(
            [_receipt("cache/a/return.bin"), _receipt("cache/b/return.bin", b"b")]
        )

    assert registry.files == {}
    assert registry.removed == registry.added
    assert transfer._TICKETS == {}


def test_release_attempts_all_files_and_retries_failed_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    isolated_transfer_state: tuple[_Clock, list[_Timer]],
) -> None:
    clock, timers = isolated_transfer_state
    registry = _Registry()
    _install_context(monkeypatch, _Context(registry))
    ticket = transfer.create_ticket(
        [_receipt("cache/a/return.bin"), _receipt("cache/b/return.bin", b"b")]
    )
    registry.fail_remove.add(registry.added[0].filename)

    with pytest.raises(TransferError, match="failed to release 1"):
        transfer.release(ticket.id)

    assert registry.removed == registry.added
    assert list(registry.files) == [registry.added[0].filename]

    registry.fail_remove.clear()
    clock.advance(transfer._CLEANUP_RETRY_SECONDS)
    timers[-1].fire()
    assert registry.files == {}
    assert transfer.release(ticket.id) is False


def test_identical_duplicate_receipts_share_one_virtual_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _Registry()
    _install_context(monkeypatch, _Context(registry))
    receipt = _receipt("cache/a/return.bin")

    ticket = transfer.create_ticket([receipt, receipt])

    assert len(ticket.assets) == 1
    assert len(registry.added) == 1
    assert transfer.release(ticket.id) is True


def test_conflicting_duplicate_receipts_fail_before_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _Registry()
    _install_context(monkeypatch, _Context(registry))

    with pytest.raises(TransferError, match="conflicting transfer receipts"):
        transfer.create_ticket(
            [
                _receipt("cache/a/return.bin", b"a"),
                _receipt("cache/a/return.bin", b"b"),
            ]
        )

    assert registry.added == []


def test_ticket_ids_ttl_and_urls_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _Registry()
    _install_context(monkeypatch, _Context(registry))

    ticket = transfer.create_ticket([_receipt("cache/a/return.bin")])

    assert len(ticket.id) == 32
    assert ticket.id.isascii() and ticket.id.isalnum()
    assert 0 < len(ticket.assets[0].url) <= transfer._MAX_VIRTUAL_FILE_URL_LENGTH
    assert ticket.assets[0].url.startswith("./@file/")
    assert not ticket.assets[0].url.startswith("data:")

    with pytest.raises(ValueError, match="at most"):
        transfer.create_ticket(
            [_receipt("cache/b/return.bin")],
            ttl_seconds=transfer._MAX_TTL_SECONDS + 1,
        )
    assert transfer.release(ticket.id) is True


def test_ticket_id_collision_attempts_are_bounded_and_cleaned_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _Registry()
    _install_context(monkeypatch, _Context(registry))
    monkeypatch.setattr(transfer, "_new_ticket_id", lambda: "a" * 32)
    first = transfer.create_ticket([_receipt("cache/a/return.bin")])

    with pytest.raises(TransferError, match="unique transfer ticket"):
        transfer.create_ticket([_receipt("cache/b/return.bin", b"b")])

    assert list(registry.files) == [registry.added[0].filename]
    assert registry.removed == [registry.added[1]]
    assert transfer.release(first.id) is True


def test_rejects_data_url_fallback_and_unsupported_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _Registry()
    _install_context(monkeypatch, _Context(registry, virtual_files_supported=False))
    with pytest.raises(TransferError, match="cannot serve virtual files"):
        transfer.create_ticket([_receipt("cache/a/return.bin")])

    _install_context(monkeypatch, _Context(registry))
    monkeypatch.setattr(
        transfer,
        "_new_virtual_file",
        lambda envelope: _VirtualFile(
            filename="fallback.bin",
            buffer=envelope,
            url="data:application/octet-stream;base64,ZW52ZWxvcGU=",
        ),
    )
    with pytest.raises(TransferError, match="relative @file URL"):
        transfer.create_ticket([_receipt("cache/a/return.bin")])
    assert registry.added == []


@pytest.mark.parametrize(
    ("receipt", "message"),
    [
        (_receipt("cache/a/return.txt"), "invalid .bin"),
        (_Receipt(_AssetRef("cache/a/return.bin", "0" * 64, 8), b"envelope"), "SHA-256"),
        (
            _Receipt(
                _AssetRef("cache/a/return.bin", hashlib.sha256(b"").hexdigest(), 0),
                b"",
            ),
            "size",
        ),
    ],
)
def test_rejects_invalid_envelopes_before_registration(
    monkeypatch: pytest.MonkeyPatch,
    receipt: _Receipt,
    message: str,
) -> None:
    registry = _Registry()
    _install_context(monkeypatch, _Context(registry))

    with pytest.raises(TransferError, match=message):
        transfer.create_ticket(cast("list[CacheAssetReceipt]", [receipt]))

    assert registry.added == []


@pytest.mark.parametrize(
    "key",
    [
        "../return.bin",
        "/return.bin",
        "cache//return.bin",
        "cache/./return.bin",
        "cache/return.bin:payload.bin",
        "cache/report<draft>/return.bin",
        "cache/report>draft/return.bin",
        'cache/report"draft/return.bin',
        "cache/report|draft/return.bin",
        "cache/report?draft/return.bin",
        "cache/report*draft/return.bin",
        "cache/control\x1f/return.bin",
        "cache/control\x7f/return.bin",
        "cache/trailing./return.bin",
        "cache/trailing /return.bin",
        "cache/CON/return.bin",
        "cache/CON.bin",
        "cache/prn.txt/return.bin",
        "cache/AUX/return.bin",
        "cache/nul.data/return.bin",
        "cache/COM1/return.bin",
        "cache/com9.txt/return.bin",
        "cache/LPT1/return.bin",
        "cache/lpt9.log/return.bin",
        "cache\\return.bin",
    ],
)
def test_rejects_nonportable_cache_asset_components_before_registration(
    monkeypatch: pytest.MonkeyPatch,
    key: str,
) -> None:
    registry = _Registry()
    _install_context(monkeypatch, _Context(registry))

    with pytest.raises(TransferError, match=r"invalid \.bin cache asset key"):
        transfer.create_ticket([_receipt(key)])

    assert registry.added == []
