from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import pytest
from marimo_export._secure_io import (
    SecureFileSizeError,
    SecureReadError,
    SecureReadLimitError,
    read_export_asset,
    read_export_index,
)


def _root(tmp_path: Path) -> Path:
    root = (tmp_path / "export").absolute()
    (root / "assets").mkdir(parents=True)
    (root / "index.json").write_bytes(b"{}")
    (root / "assets" / "asset.bin").write_bytes(b"payload")
    return root


def test_secure_reader_reads_regular_index_and_asset_files(tmp_path: Path) -> None:
    root = _root(tmp_path)

    assert read_export_index(root, max_bytes=2) == b"{}"
    assert (
        read_export_asset(
            root,
            "assets/asset.bin",
            expected_size=7,
            max_bytes=7,
        )
        == b"payload"
    )


def test_secure_reader_enforces_index_and_asset_limits(tmp_path: Path) -> None:
    root = _root(tmp_path)

    with pytest.raises(SecureReadLimitError):
        read_export_index(root, max_bytes=1)
    with pytest.raises(SecureReadLimitError):
        read_export_asset(
            root,
            "assets/asset.bin",
            expected_size=7,
            max_bytes=6,
        )


def test_secure_reader_requires_the_declared_asset_size(tmp_path: Path) -> None:
    root = _root(tmp_path)

    with pytest.raises(SecureFileSizeError):
        read_export_asset(
            root,
            "assets/asset.bin",
            expected_size=6,
            max_bytes=7,
        )


@pytest.mark.parametrize(
    "path",
    [
        "asset.bin",
        "cache/asset.bin",
        "assets/nested/asset.bin",
        "assets/../asset.bin",
        r"assets\asset.bin",
    ],
)
def test_secure_reader_accepts_only_a_derived_asset_path(
    tmp_path: Path,
    path: str,
) -> None:
    root = _root(tmp_path)

    with pytest.raises(SecureReadError):
        read_export_asset(root, path, expected_size=7, max_bytes=7)


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX symlinks")
def test_secure_reader_does_not_follow_index_or_asset_symlinks(tmp_path: Path) -> None:
    root = _root(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(b"payload")

    (root / "index.json").unlink()
    (root / "index.json").symlink_to(outside)
    with pytest.raises(SecureReadError):
        read_export_index(root, max_bytes=100)

    (root / "assets" / "asset.bin").unlink()
    (root / "assets" / "asset.bin").symlink_to(outside)
    with pytest.raises(SecureReadError):
        read_export_asset(
            root,
            "assets/asset.bin",
            expected_size=7,
            max_bytes=7,
        )


def test_secure_reader_requires_an_absolute_root(tmp_path: Path) -> None:
    del tmp_path
    with pytest.raises(SecureReadError):
        read_export_index(Path("relative"), max_bytes=1)


@pytest.mark.parametrize("value", [True, 0])
def test_secure_reader_requires_positive_limits(tmp_path: Path, value: object) -> None:
    root = _root(tmp_path)
    with pytest.raises(TypeError):
        read_export_index(root, max_bytes=cast(Any, value))
