from __future__ import annotations

import hashlib
from dataclasses import dataclass
from types import SimpleNamespace

import marimo_export._marimo.transfer as transfer
import pytest
from marimo_export._diagnostics import cleanup_failures
from marimo_export._marimo.capabilities import NativeReceipt
from marimo_export._marimo.transfer import _MAX_ASSETS_PER_TICKET, _payloads, create_ticket, release
from marimo_export.descriptors import AssetRef, NumpyDescriptor, Provenance, ScalarDescriptor
from marimo_export.errors import IntegrityError


def _provenance() -> Provenance:
    return Provenance(python_type="builtins.int")


@dataclass
class _File:
    filename: str
    buffer: bytes
    url: str


class _Registry:
    def __init__(self) -> None:
        self.files: dict[str, _File] = {}
        self.add_calls = 0
        self.remove_calls: list[str] = []
        self.cancel_add_at: int | None = None
        self.add_failure: BaseException = KeyboardInterrupt("registration cancelled")
        self.remove_failures: dict[str, BaseException] = {}

    def add(self, virtual_file: _File, context: object) -> None:
        del context
        self.add_calls += 1
        self.files[virtual_file.filename] = virtual_file
        if self.cancel_add_at == self.add_calls:
            raise self.add_failure

    def has(self, filename: str) -> bool:
        return filename in self.files

    def remove(self, virtual_file: _File) -> None:
        self.remove_calls.append(virtual_file.filename)
        failure = self.remove_failures.get(virtual_file.filename)
        if failure is not None:
            raise failure
        self.files.pop(virtual_file.filename, None)


class _Host:
    def __init__(self, registry: _Registry) -> None:
        self.registry = registry
        self.position = 0

    def context(self) -> object:
        return SimpleNamespace(
            virtual_files_supported=True,
            virtual_file_registry=self.registry,
        )

    def create_virtual_file(self, data: bytes) -> _File:
        self.position += 1
        filename = f"asset-{self.position}.bin"
        return _File(
            filename=filename,
            buffer=data,
            url=f"./@file/{len(data)}-{filename}",
        )


def _asset_receipt(name: str, payload: bytes) -> NativeReceipt:
    digest = hashlib.sha256(payload).hexdigest()
    return NativeReceipt(
        output=name,
        descriptor=NumpyDescriptor(
            asset=AssetRef(digest, len(payload)),
            provenance=Provenance(python_type="numpy.ndarray"),
        ),
        payload=payload,
        disposition="miss",
    )


def test_transfer_registration_cancellation_retains_failed_cleanup_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _Registry()
    registry.cancel_add_at = 2
    registry.remove_failures["asset-1.bin"] = RuntimeError("remove failed")
    host = _Host(registry)
    monkeypatch.setattr(transfer, "_schedule_locked", lambda: None)
    transfer._TICKETS.clear()
    cancellation = KeyboardInterrupt("registration cancelled")
    registry.add_failure = cancellation
    try:
        with pytest.raises(KeyboardInterrupt) as raised:
            create_ticket(
                [_asset_receipt("first", b"first"), _asset_receipt("second", b"second")],
                host=host,
            )

        assert raised.value is cancellation
        assert registry.remove_calls == ["asset-1.bin", "asset-2.bin"]
        assert cleanup_failures(cancellation) == (
            "transfer registration cleanup also failed: RuntimeError",
        )
        assert len(transfer._TICKETS) == 1
        recovery = next(iter(transfer._TICKETS.values()))
        assert [item.filename for item in recovery.files] == ["asset-1.bin"]
        assert recovery.byte_count == len(recovery.files[0].buffer)
        assert transfer._active_transfer_bytes_locked() == recovery.byte_count
    finally:
        transfer._TICKETS.clear()


def test_transfer_release_cancellation_retains_unreleased_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _Registry()
    host = _Host(registry)
    monkeypatch.setattr(transfer, "_schedule_locked", lambda: None)
    transfer._TICKETS.clear()
    try:
        ticket = create_ticket(
            [
                _asset_receipt("first", b"first"),
                _asset_receipt("second", b"second"),
                _asset_receipt("third", b"third"),
            ],
            host=host,
        )
        cancellation = KeyboardInterrupt("release cancelled")
        registry.remove_failures["asset-2.bin"] = cancellation

        with pytest.raises(KeyboardInterrupt) as raised:
            release(ticket.id)

        assert raised.value is cancellation
        assert registry.remove_calls == ["asset-1.bin", "asset-2.bin", "asset-3.bin"]
        assert ticket.id in transfer._TICKETS
        assert [item.filename for item in transfer._TICKETS[ticket.id].files] == ["asset-2.bin"]
        assert transfer._TICKETS[ticket.id].byte_count == len(
            transfer._TICKETS[ticket.id].files[0].buffer
        )
        registry.remove_failures.clear()
        assert release(ticket.id)
        assert registry.files == {}
        assert transfer._active_transfer_bytes_locked() == 0
    finally:
        transfer._TICKETS.clear()


def test_transfer_release_prioritizes_cancellation_after_ordinary_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _Registry()
    host = _Host(registry)
    monkeypatch.setattr(transfer, "_schedule_locked", lambda: None)
    transfer._TICKETS.clear()
    try:
        ticket = create_ticket(
            [_asset_receipt("first", b"first"), _asset_receipt("second", b"second")],
            host=host,
        )
        registry.remove_failures["asset-1.bin"] = RuntimeError("ordinary failure")
        cancellation = KeyboardInterrupt("release cancelled")
        registry.remove_failures["asset-2.bin"] = cancellation

        with pytest.raises(KeyboardInterrupt) as raised:
            release(ticket.id)

        assert raised.value is cancellation
        assert cleanup_failures(cancellation) == (
            "transfer release cleanup also failed: RuntimeError",
        )
        assert [item.filename for item in transfer._TICKETS[ticket.id].files] == [
            "asset-1.bin",
            "asset-2.bin",
        ]
    finally:
        transfer._TICKETS.clear()


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
            provenance=Provenance(python_type="numpy.ndarray"),
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
                    provenance=Provenance(python_type="numpy.ndarray"),
                ),
                payload=value,
                disposition="hit",
            )
        )
    with pytest.raises(IntegrityError, match="at most 4096 assets"):
        _payloads(unique)


def test_transfer_payload_byte_limits_apply_before_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _Registry()
    host = _Host(registry)
    monkeypatch.setattr(transfer, "MAX_EXPORT_ASSET_BYTES", 8)
    monkeypatch.setattr(transfer, "MAX_EXPORT_CLOSURE_BYTES", 10)

    with pytest.raises(IntegrityError, match="at most 8 bytes"):
        create_ticket([_asset_receipt("large", b"123456789")], host=host)
    with pytest.raises(IntegrityError, match="at most 10 bytes"):
        create_ticket(
            [
                _asset_receipt("first", b"123456"),
                _asset_receipt("second", b"12345"),
            ],
            host=host,
        )
    assert registry.add_calls == 0


def test_transfer_process_caps_release_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _Registry()
    host = _Host(registry)
    monkeypatch.setattr(transfer, "_schedule_locked", lambda: None)
    monkeypatch.setattr(transfer, "_MAX_ACTIVE_TICKETS", 2)
    monkeypatch.setattr(transfer, "_MAX_ACTIVE_TRANSFER_BYTES", 10)
    transfer._TICKETS.clear()
    try:
        first = create_ticket([_asset_receipt("first", b"123456")], host=host)
        with pytest.raises(IntegrityError, match="retain at most 10 bytes"):
            create_ticket([_asset_receipt("overflow", b"12345")], host=host)
        second = create_ticket([_asset_receipt("second", b"1234")], host=host)
        assert transfer._active_transfer_bytes_locked() == 10

        with pytest.raises(IntegrityError, match="at most 2 transfer tickets"):
            create_ticket([], host=host)

        assert release(first.id)
        replacement = create_ticket([_asset_receipt("replacement", b"123456")], host=host)
        assert transfer._active_transfer_bytes_locked() == 10
        assert release(second.id)
        assert release(replacement.id)
        assert transfer._active_transfer_bytes_locked() == 0
    finally:
        transfer._TICKETS.clear()


def test_abandoned_ticket_is_swept_before_process_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _Registry()
    host = _Host(registry)
    now = 10.0
    monkeypatch.setattr(transfer, "_schedule_locked", lambda: None)
    monkeypatch.setattr(transfer, "_MAX_ACTIVE_TICKETS", 1)
    monkeypatch.setattr(transfer, "_monotonic", lambda: now)
    transfer._TICKETS.clear()
    try:
        first = create_ticket(
            [_asset_receipt("first", b"first")],
            host=host,
            ttl_seconds=1,
        )
        now = 12.0
        second = create_ticket(
            [_asset_receipt("second", b"second")],
            host=host,
            ttl_seconds=1,
        )

        assert first.id not in transfer._TICKETS
        assert second.id in transfer._TICKETS
        assert "asset-1.bin" not in registry.files
        assert transfer._active_transfer_bytes_locked() == len(b"second")
    finally:
        transfer._TICKETS.clear()
