from __future__ import annotations

import pytest
from marimo_export._portable import (
    MAX_ASSET_KEY_UTF8_BYTES,
    asset_key_components,
    validate_asset_key,
    validate_notebook_basename,
    validate_portable_basename,
)


def test_portable_basename_accepts_cross_platform_names() -> None:
    for name in ("summary.json", ".hidden", "résumé.pdf", "é" * 127):
        assert validate_portable_basename(name, "filename") == name


def test_portable_basename_rejects_unsafe_names() -> None:
    for name in (
        "",
        "..",
        " summary.json",
        "summary.",
        "path/name.json",
        r"path\name.json",
        "stream:name.json",
        "control\x00.json",
        "CON",
        "é" * 128,
        "\ud800",
    ):
        with pytest.raises((TypeError, ValueError)):
            validate_portable_basename(name, "filename")


def test_notebook_basename_preserves_active_source_names() -> None:
    for name in ("finance.py", "report?.py", "NUL.py", " report.py "):
        assert validate_notebook_basename(name, "notebook.filename") == name

    for name in ("", "folder/report.py", "report\x00.py", "\ud800"):
        with pytest.raises((TypeError, ValueError)):
            validate_notebook_basename(name, "notebook.filename")


def test_asset_key_is_portable_and_size_bounded() -> None:
    key = "project/cache/return.bin"
    assert validate_asset_key(key, "asset.key") == key
    assert asset_key_components(key, "asset.key") == ("project", "cache", "return.bin")

    boundary = "/".join(
        [
            "a" * 255,
            "b" * 255,
            "c" * 255,
            "d",
            "e" * 250 + ".bin",
        ]
    )
    assert len(boundary.encode()) == MAX_ASSET_KEY_UTF8_BYTES
    assert validate_asset_key(boundary, "asset.key") == boundary

    for invalid in (
        "project/return.json",
        "project//return.bin",
        "project/../return.bin",
        "project/CON/return.bin",
        r"project\return.bin",
        boundary.replace("e" * 250, "e" * 251),
    ):
        with pytest.raises((TypeError, ValueError)):
            validate_asset_key(invalid, "asset.key")
