from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from marimo_export._json import JsonObject, sha256_bytes
from marimo_export._marimo import cache, delivery
from marimo_export.index import (
    ExportIndex,
    ExportRef,
    PayloadRef,
    ProducerInfo,
    ProjectionEntry,
    ScenarioIndex,
    export_ref,
)


class MemoryStore:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def get(self, key: str) -> bytes | None:
        return self.values.get(key)

    def put(self, key: str, value: bytes) -> bool:
        self.values[key] = value
        return True


@pytest.fixture(autouse=True)
def _clear_delivery_leases() -> Iterator[None]:
    def reset() -> None:
        with delivery._LEASE_LOCK:
            if delivery._SCHEDULER_TIMER is not None:
                delivery._SCHEDULER_TIMER.cancel()
            delivery._SCHEDULER_TIMER = None
            delivery._SCHEDULER_TOKEN = None
            delivery._LEASES.clear()
        with delivery._ROOT_LOCKS_GUARD:
            delivery._ROOT_LOCKS.clear()

    reset()
    yield
    reset()


def _index(payload: bytes) -> ExportIndex:
    digest = sha256_bytes(payload)
    inputs: JsonObject = {}
    return ExportIndex(
        notebook_name="notebook.py",
        notebook_source_sha256="a" * 64,
        plan_sha256="b" * 64,
        producer=ProducerInfo("0.23.14", "0.0.0"),
        scenarios=(
            ScenarioIndex(
                id="default",
                inputs=inputs,
                outputs={
                    "value": {
                        "bytes": ProjectionEntry(
                            format_id="bytes.v1",
                            media_type="application/octet-stream",
                            metadata={},
                            payload=PayloadRef(
                                key=f"marimo-export/payloads/sha256/{digest}",
                                sha256=digest,
                                size=len(payload),
                            ),
                        )
                    }
                },
            ),
        ),
    )


def _prepare_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: bytes = b"portable",
) -> tuple[ExportRef, bytes, str]:
    store = MemoryStore()
    monkeypatch.setattr(cache, "cache_store", lambda: store)
    monkeypatch.setattr(delivery, "notebook_path", lambda: tmp_path / "notebook.py")
    payload_key, _, _ = cache.put_payload(payload)
    index = _index(payload)
    ref, index_bytes = export_ref(index)
    store.put(ref.key, index_bytes)
    return ref, index_bytes, payload_key


def test_stage_materializes_one_verified_directory_and_release_removes_it(
    monkeypatch, tmp_path: Path
) -> None:
    payload = b"portable"
    ref, index_bytes, payload_key = _prepare_stage(monkeypatch, tmp_path, payload)

    result = delivery.stage(ref)
    stage_id = result["id"]
    assert isinstance(stage_id, str)
    root = tmp_path / "public" / ".marimo-export" / stage_id
    assert (root / "index.json").read_bytes() == index_bytes
    assert (root / "cache" / payload_key).read_bytes() == payload
    assert {child.relative_to(root).as_posix() for child in root.rglob("*") if child.is_file()} == {
        "index.json",
        f"cache/{payload_key}",
    }
    expires_at_ms = result["expires_at_ms"]
    assert isinstance(expires_at_ms, int)
    assert expires_at_ms > int(time.time() * 1000)
    assert result["url"] == f"./public/.marimo-export/{stage_id}/"

    assert delivery.release(stage_id) is True
    assert not root.exists()
    assert delivery.release(stage_id) is False


def test_empty_payload_is_materialized_as_empty_portable_bytes(monkeypatch, tmp_path: Path) -> None:
    ref, _, payload_key = _prepare_stage(monkeypatch, tmp_path, b"")

    result = delivery.stage(ref)
    stage_id = result["id"]
    assert isinstance(stage_id, str)
    target = tmp_path / "public" / ".marimo-export" / stage_id / "cache" / payload_key
    assert target.read_bytes() == b""
    assert delivery.release(stage_id) is True


def test_stage_preserves_an_active_lease_during_orphan_collection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ref, _, _ = _prepare_stage(monkeypatch, tmp_path)
    first = delivery.stage(ref)
    first_id = first["id"]
    assert isinstance(first_id, str)
    root = tmp_path / "public" / ".marimo-export"
    first_path = root / first_id
    stale = time.time() - delivery._STAGE_TTL_SECONDS * 2
    os.utime(first_path, (stale, stale))

    second = delivery.stage(ref)
    second_id = second["id"]
    assert isinstance(second_id, str)
    assert first_path.is_dir()

    assert delivery.release(first_id) is True
    assert delivery.release(second_id) is True


def test_stage_expires_without_another_remote_operation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(delivery, "_STAGE_TTL_SECONDS", 0.05)
    ref, _, _ = _prepare_stage(monkeypatch, tmp_path)
    result = delivery.stage(ref)
    stage_id = result["id"]
    assert isinstance(stage_id, str)
    target = tmp_path / "public" / ".marimo-export" / stage_id

    deadline = time.monotonic() + 2
    while target.exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert not target.exists()
    assert delivery.release(stage_id) is False


def test_unexpired_restart_orphan_keeps_its_persisted_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(delivery, "_STAGE_TTL_SECONDS", 0.2)
    ref, _, _ = _prepare_stage(monkeypatch, tmp_path)
    first = delivery.stage(ref)
    first_id = first["id"]
    first_expiry = first["expires_at_ms"]
    assert isinstance(first_id, str)
    assert isinstance(first_expiry, int)
    root = tmp_path / "public" / ".marimo-export"
    first_path = root / first_id

    with delivery._LEASE_LOCK:
        delivery._LEASES.pop((root, first_id))
        delivery._schedule_locked()

    assert delivery.release("c" * 32) is False
    adopted = delivery._LEASES[(root, first_id)]
    assert adopted.expires_at_ms == first_expiry
    assert first_path.is_dir()

    deadline = time.monotonic() + 2
    while first_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert not first_path.exists()


def test_stage_waits_for_a_foreign_pending_owner_before_collection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ref, _, _ = _prepare_stage(monkeypatch, tmp_path)
    root = tmp_path / "public" / ".marimo-export"
    root.mkdir(parents=True)
    pending = root / f".{('e' * 32)}.tmp"
    script = """
from pathlib import Path
import sys
from marimo_export._marimo.delivery import _locked_root

root = Path(sys.argv[1])
pending = root / sys.argv[2]
with _locked_root(root):
    pending.mkdir()
    print("ready", flush=True)
    sys.stdin.readline()
"""
    owner = subprocess.Popen(
        [sys.executable, "-c", script, str(root), pending.name],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert owner.stdout is not None
        assert owner.stdout.readline().strip() == "ready"
        with ThreadPoolExecutor(max_workers=1) as executor:
            staging = executor.submit(delivery.stage, ref)
            time.sleep(0.05)
            assert not staging.done()
            assert pending.is_dir()
            assert owner.stdin is not None
            owner.stdin.write("\n")
            owner.stdin.flush()
            assert owner.wait(timeout=2) == 0
            result = staging.result(timeout=2)
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=2)

    stage_id = result["id"]
    assert isinstance(stage_id, str)
    assert not pending.exists()

    assert delivery.release(stage_id) is True


def test_stage_collects_an_abandoned_pending_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ref, _, _ = _prepare_stage(monkeypatch, tmp_path)
    root = tmp_path / "public" / ".marimo-export"
    root.mkdir(parents=True)
    pending = root / f".{('e' * 32)}.tmp"
    pending.mkdir()

    result = delivery.stage(ref)
    stage_id = result["id"]
    assert isinstance(stage_id, str)
    assert not pending.exists()
    assert delivery.release(stage_id) is True


def test_concurrent_stages_keep_independent_leases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ref, _, _ = _prepare_stage(monkeypatch, tmp_path)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: delivery.stage(ref), range(4)))

    stage_ids: list[str] = []
    for result in results:
        stage_id = result["id"]
        assert isinstance(stage_id, str)
        stage_ids.append(stage_id)
    assert len(set(stage_ids)) == 4
    root = tmp_path / "public" / ".marimo-export"
    assert all((root / stage_id).is_dir() for stage_id in stage_ids)

    for stage_id in stage_ids:
        assert delivery.release(stage_id) is True


def test_concurrent_release_removes_a_stage_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ref, _, _ = _prepare_stage(monkeypatch, tmp_path)
    result = delivery.stage(ref)
    stage_id = result["id"]
    assert isinstance(stage_id, str)

    with ThreadPoolExecutor(max_workers=2) as executor:
        releases = list(executor.map(lambda _: delivery.release(stage_id), range(2)))

    assert sorted(releases) == [False, True]


def test_stage_collects_an_expired_orphan_after_process_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ref, _, _ = _prepare_stage(monkeypatch, tmp_path)
    root = tmp_path / "public" / ".marimo-export"
    root.mkdir(parents=True)
    orphan = root / ("f" * 32)
    orphan.mkdir()
    (orphan / "index.json").write_bytes(b"orphan")
    stale = time.time() - delivery._STAGE_TTL_SECONDS * 2
    os.utime(orphan, (stale, stale))

    result = delivery.stage(ref)
    stage_id = result["id"]
    assert isinstance(stage_id, str)
    assert not orphan.exists()
    assert delivery.release(stage_id) is True


def test_release_collects_an_expired_orphan_after_process_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare_stage(monkeypatch, tmp_path)
    root = tmp_path / "public" / ".marimo-export"
    root.mkdir(parents=True)
    orphan = root / ("d" * 32)
    orphan.mkdir()
    stale = time.time() - delivery._STAGE_TTL_SECONDS * 2
    os.utime(orphan, (stale, stale))

    assert delivery.release("c" * 32) is False
    assert not orphan.exists()


def test_expiry_retries_after_a_transient_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(delivery, "_STAGE_TTL_SECONDS", 0.05)
    monkeypatch.setattr(delivery, "_CLEANUP_RETRY_SECONDS", 0.01)
    ref, _, _ = _prepare_stage(monkeypatch, tmp_path)
    result = delivery.stage(ref)
    stage_id = result["id"]
    assert isinstance(stage_id, str)
    target = tmp_path / "public" / ".marimo-export" / stage_id
    original_rmtree = delivery.shutil.rmtree
    failed = False

    def flaky_rmtree(path: Path) -> None:
        nonlocal failed
        if Path(path) == target and not failed:
            failed = True
            raise OSError("transient cleanup failure")
        original_rmtree(path)

    monkeypatch.setattr(delivery.shutil, "rmtree", flaky_rmtree)
    deadline = time.monotonic() + 2
    while target.exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert failed is True
    assert not target.exists()
    assert delivery.release(stage_id) is False


def test_stage_root_symlink_cannot_redirect_stage_or_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ref, _, _ = _prepare_stage(monkeypatch, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    public = tmp_path / "public"
    public.mkdir()
    root = public / ".marimo-export"
    root.symlink_to(outside, target_is_directory=True)
    stage_id = "a" * 32
    victim = outside / stage_id
    victim.mkdir()
    (victim / "sentinel").write_text("keep")

    with pytest.raises(RuntimeError, match="stage root must be a directory"):
        delivery.stage(ref)
    with pytest.raises(RuntimeError, match="stage root must be a directory"):
        delivery.release(stage_id)

    assert (victim / "sentinel").read_text() == "keep"
