from __future__ import annotations

import io
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from marimo_export import _secure_io
from marimo_export._secure_io import (
    SecureFileSizeError,
    SecureReadError,
    SecureReadLimitError,
    read_cache_asset,
    read_publication_index,
)


def _publication(root: Path) -> tuple[Path, Path]:
    asset = root / "cache" / "project" / "abc" / "return.bin"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"asset")
    (root / "index.json").write_bytes(b"index")
    return root.resolve(strict=True), asset


def _use_windows_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_secure_io, "_descriptor_relative_reads_supported", lambda: False)
    monkeypatch.setattr(_secure_io, "_windows_fallback_supported", lambda: True)


_POSIX_DESCRIPTOR_READ = pytest.mark.skipif(
    os.name != "posix",
    reason="requires POSIX descriptor-relative opens",
)


def test_reads_index_and_exact_sized_asset_from_verified_descriptors(tmp_path: Path) -> None:
    root, _ = _publication(tmp_path / "publication")

    assert read_publication_index(root, max_bytes=5) == b"index"
    assert (
        read_cache_asset(
            root,
            "project/abc/return.bin",
            expected_size=5,
            max_bytes=5,
        )
        == b"asset"
    )


def test_index_read_enforces_the_inspected_size_limit(tmp_path: Path) -> None:
    root, _ = _publication(tmp_path / "publication")

    with pytest.raises(SecureReadLimitError) as raised:
        read_publication_index(root, max_bytes=4)

    assert raised.value.actual_size == 5
    assert raised.value.limit == 4


def test_asset_read_checks_indexed_size_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _publication(tmp_path / "publication")

    def unexpected_read(_file_fd: int, _size: int) -> bytes:
        raise AssertionError("asset bytes were read before the size check")

    monkeypatch.setattr(_secure_io, "_read_file_bytes", unexpected_read)

    with pytest.raises(SecureFileSizeError) as raised:
        read_cache_asset(
            root,
            "project/abc/return.bin",
            expected_size=6,
            max_bytes=10,
        )

    assert raised.value.expected_size == 6
    assert raised.value.actual_size == 5


def test_asset_read_rejects_indexed_size_over_the_limit_before_opening(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _publication(tmp_path / "publication")

    def unexpected_open(*_args: Any, **_kwargs: Any) -> int:
        raise AssertionError("asset path was opened before the limit check")

    monkeypatch.setattr(_secure_io.os, "open", unexpected_open)

    with pytest.raises(SecureReadLimitError) as raised:
        read_cache_asset(
            root,
            "project/abc/return.bin",
            expected_size=6,
            max_bytes=5,
        )

    assert raised.value.actual_size == 6
    assert raised.value.limit == 5


def test_huge_limit_never_becomes_an_os_read_allocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _publication(tmp_path / "publication")
    real_read = _secure_io._read_file_bytes
    requested_sizes: list[int] = []
    returned_buffers: list[bytes] = []

    def recording_read(file_fd: int, size: int) -> bytes:
        requested_sizes.append(size)
        value = real_read(file_fd, size)
        returned_buffers.append(value)
        return value

    monkeypatch.setattr(_secure_io, "_read_file_bytes", recording_read)

    data = read_publication_index(root, max_bytes=10**100)

    assert data == b"index"
    assert data is returned_buffers[0]
    assert requested_sizes == [6]


def test_rejects_symlinked_directory_component(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "return.bin").write_bytes(b"outside")
    root = tmp_path / "publication"
    (root / "cache").mkdir(parents=True)
    try:
        (root / "cache" / "project").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable")

    with pytest.raises(SecureReadError):
        read_cache_asset(
            root.resolve(strict=True),
            "project/return.bin",
            expected_size=7,
            max_bytes=7,
        )


def test_rejects_symlinked_final_file(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"outside")
    root = tmp_path / "publication"
    root.mkdir()
    try:
        (root / "index.json").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable")

    with pytest.raises(SecureReadError):
        read_publication_index(root.resolve(strict=True), max_bytes=100)


def test_rejects_a_non_regular_final_file(tmp_path: Path) -> None:
    root = tmp_path / "publication"
    (root / "index.json").mkdir(parents=True)

    with pytest.raises(SecureReadError, match="not a regular file"):
        read_publication_index(root.resolve(strict=True), max_bytes=100)


@_POSIX_DESCRIPTOR_READ
def test_leaf_replacement_before_open_cannot_follow_outside_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _publication(tmp_path / "publication")
    leaf = root / "index.json"
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"outside-secret")
    real_open_file_at = _secure_io._open_file_at
    replaced = False

    def replacing_open_file_at(parent_fd: int, component: str) -> int:
        nonlocal replaced
        if component == "index.json" and not replaced:
            replaced = True
            leaf.unlink()
            try:
                leaf.symlink_to(outside)
            except OSError:
                pytest.skip("symlinks are unavailable")
        return real_open_file_at(parent_fd, component)

    monkeypatch.setattr(_secure_io, "_open_file_at", replacing_open_file_at)

    with pytest.raises(SecureReadError):
        read_publication_index(root, max_bytes=100)
    assert replaced


@_POSIX_DESCRIPTOR_READ
def test_leaf_replacement_after_open_reads_the_same_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _publication(tmp_path / "publication")
    leaf = root / "index.json"
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"outside-secret")
    real_read = _secure_io._read_file_bytes
    replaced = False

    def replacing_read(file_fd: int, size: int) -> bytes:
        nonlocal replaced
        if not replaced:
            replaced = True
            leaf.unlink()
            try:
                leaf.symlink_to(outside)
            except OSError:
                pytest.skip("symlinks are unavailable")
        return real_read(file_fd, size)

    monkeypatch.setattr(_secure_io, "_read_file_bytes", replacing_read)

    assert read_publication_index(root, max_bytes=100) == b"index"
    assert replaced
    assert leaf.resolve(strict=True) == outside


def test_buffered_read_handles_short_raw_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _publication(tmp_path / "publication")

    class ShortReadRaw(io.RawIOBase):
        def __init__(self) -> None:
            self._data = memoryview(b"index")
            self._offset = 0

        def readable(self) -> bool:
            return True

        def readinto(self, buffer: Any) -> int:
            if self._offset == len(self._data):
                return 0
            length = min(2, len(buffer), len(self._data) - self._offset)
            buffer[:length] = self._data[self._offset : self._offset + length]
            self._offset += length
            return length

    monkeypatch.setattr(
        _secure_io.io,
        "FileIO",
        lambda *_args, **_kwargs: ShortReadRaw(),
    )

    assert read_publication_index(root, max_bytes=100) == b"index"


@_POSIX_DESCRIPTOR_READ
def test_rejects_platforms_without_descriptor_relative_no_follow_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _publication(tmp_path / "publication")
    monkeypatch.setattr(_secure_io.os, "supports_dir_fd", set())

    with pytest.raises(SecureReadError, match="unavailable on this platform"):
        read_publication_index(root, max_bytes=100)


def test_windows_fallback_reads_index_and_asset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _publication(tmp_path / "publication")
    _use_windows_fallback(monkeypatch)

    assert read_publication_index(root, max_bytes=5) == b"index"
    assert (
        read_cache_asset(
            root,
            "project/abc/return.bin",
            expected_size=5,
            max_bytes=5,
        )
        == b"asset"
    )


def test_windows_fallback_rejects_static_symlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _publication(tmp_path / "publication")
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"outside")
    leaf = root / "index.json"
    leaf.unlink()
    try:
        leaf.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable")
    _use_windows_fallback(monkeypatch)

    with pytest.raises(SecureReadError, match="reparse points"):
        read_publication_index(root, max_bytes=100)


def test_windows_fallback_rejects_symlinked_directory_components(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "return.bin").write_bytes(b"outside")
    root = tmp_path / "publication"
    (root / "cache").mkdir(parents=True)
    try:
        (root / "cache" / "project").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable")
    _use_windows_fallback(monkeypatch)

    with pytest.raises(SecureReadError, match="reparse points"):
        read_cache_asset(
            root.resolve(strict=True),
            "project/return.bin",
            expected_size=7,
            max_bytes=7,
        )


def test_windows_fallback_recognizes_junction_reparse_metadata() -> None:
    inspected = SimpleNamespace(
        st_mode=stat.S_IFDIR,
        st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
    )

    with pytest.raises(SecureReadError, match="reparse points"):
        _secure_io._reject_reparse_point(inspected)


def test_windows_fallback_rejects_a_leaf_replaced_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _publication(tmp_path / "publication")
    leaf = root / "index.json"
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(b"outside")
    real_open = os.open
    replaced = False

    def replacing_open(path: str | os.PathLike[str], flags: int) -> int:
        nonlocal replaced
        if Path(path) == leaf and not replaced:
            replaced = True
            leaf.unlink()
            replacement.rename(leaf)
        return real_open(path, flags)

    _use_windows_fallback(monkeypatch)
    monkeypatch.setattr(_secure_io.os, "open", replacing_open)

    with pytest.raises(SecureReadError, match="identity changed"):
        read_publication_index(root, max_bytes=100)
    assert replaced


def test_windows_fallback_closes_the_opened_descriptor_after_a_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _publication(tmp_path / "publication")
    real_open = os.open
    opened: list[int] = []

    def recording_open(path: str | os.PathLike[str], flags: int) -> int:
        file_fd = real_open(path, flags)
        opened.append(file_fd)
        return file_fd

    _use_windows_fallback(monkeypatch)
    monkeypatch.setattr(_secure_io.os, "open", recording_open)

    with pytest.raises(SecureFileSizeError):
        read_cache_asset(
            root,
            "project/abc/return.bin",
            expected_size=6,
            max_bytes=10,
        )

    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])


def test_windows_fallback_rejects_resolved_paths_outside_the_root(tmp_path: Path) -> None:
    root = tmp_path / "publication"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"outside")

    with pytest.raises(SecureReadError, match="outside the publication"):
        _secure_io._require_inside(root.resolve(strict=True), outside)


@_POSIX_DESCRIPTOR_READ
def test_closes_every_opened_descriptor_after_a_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _publication(tmp_path / "publication")
    real_open_root = _secure_io._open_root
    real_open_directory_at = _secure_io._open_directory_at
    real_open_file_at = _secure_io._open_file_at
    opened: set[int] = set()

    def recording_open_root(components: tuple[str, ...]) -> int:
        file_fd = real_open_root(components)
        opened.add(file_fd)
        return file_fd

    def recording_open_directory_at(parent_fd: int, component: str) -> int:
        file_fd = real_open_directory_at(parent_fd, component)
        opened.add(file_fd)
        return file_fd

    def recording_open_file_at(parent_fd: int, component: str) -> int:
        file_fd = real_open_file_at(parent_fd, component)
        opened.add(file_fd)
        return file_fd

    monkeypatch.setattr(_secure_io, "_open_root", recording_open_root)
    monkeypatch.setattr(_secure_io, "_open_directory_at", recording_open_directory_at)
    monkeypatch.setattr(_secure_io, "_open_file_at", recording_open_file_at)

    with pytest.raises(SecureFileSizeError):
        read_cache_asset(
            root,
            "project/abc/return.bin",
            expected_size=6,
            max_bytes=10,
        )

    assert opened
    for file_fd in opened:
        with pytest.raises(OSError):
            os.fstat(file_fd)


def test_rejects_unverified_roots() -> None:
    with pytest.raises(ValueError):
        read_cache_asset(Path("relative"), "project/return.bin", expected_size=1, max_bytes=1)


@pytest.mark.parametrize(
    "key",
    [
        "../return.bin",
        "/return.bin",
        "project//return.bin",
        "project/./return.bin",
        "project\\return.bin",
        "project/control\x00/return.bin",
        "project/return.bin:payload.bin",
        "project/report<draft>/return.bin",
        "project/report>draft/return.bin",
        'project/report"draft/return.bin',
        "project/report|draft/return.bin",
        "project/report?draft/return.bin",
        "project/report*draft/return.bin",
        "project/control\x1f/return.bin",
        "project/control\x7f/return.bin",
        "project/trailing./return.bin",
        "project/trailing /return.bin",
        "project/CON/return.bin",
        "project/CONIN$/return.bin",
        "project/conout$.txt/return.bin",
        "project/prn.txt/return.bin",
        "project/AUX/return.bin",
        "project/nul.data/return.bin",
        "project/COM1/return.bin",
        "project/com9.txt/return.bin",
        "project/COM¹/return.bin",
        "project/com³.txt/return.bin",
        "project/LPT1/return.bin",
        "project/lpt9.log/return.bin",
        "project/LPT²/return.bin",
        "project/lpt³.log/return.bin",
    ],
)
def test_rejects_nonportable_asset_key_components(tmp_path: Path, key: str) -> None:
    root, _ = _publication(tmp_path / "publication")

    with pytest.raises(SecureReadError, match="portable path"):
        read_cache_asset(root, key, expected_size=1, max_bytes=1)
