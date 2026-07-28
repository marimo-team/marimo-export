from __future__ import annotations

import re

import pytest
from marimo_export._portable import (
    ASSET_KEY_SCHEMA_PATTERN,
    MAX_ASSET_KEY_UTF8_BYTES,
    MAX_PORTABLE_BASENAME_UTF8_BYTES,
    NOTEBOOK_BASENAME_SCHEMA_PATTERN,
    PORTABLE_BASENAME_SCHEMA_PATTERN,
    asset_key_components,
    validate_asset_key,
    validate_notebook_basename,
    validate_portable_basename,
)


@pytest.mark.parametrize(
    "name",
    [
        "summary.json",
        ".hidden",
        "résumé.pdf",
        "a" * MAX_PORTABLE_BASENAME_UTF8_BYTES,
        "é" * 127,
    ],
)
def test_strict_portable_basename_accepts_cross_platform_names(name: str) -> None:
    assert validate_portable_basename(name, "filename") == name
    assert re.fullmatch(PORTABLE_BASENAME_SCHEMA_PATTERN, name) is not None


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        " summary.json",
        "summary.json ",
        "summary.",
        "path/name.json",
        "path\\name.json",
        "stream:name.json",
        "report<draft>.json",
        "report>draft.json",
        'report"draft.json',
        "report|draft.json",
        "report?draft.json",
        "report*draft.json",
        "control\x00.json",
        "control\x1f.json",
        "control\x7f.json",
        "CON",
        "CONIN$",
        "conout$.txt",
        "prn.txt",
        "AUX.json",
        "nul.data",
        "COM1",
        "com9.txt",
        "COM¹",
        "com³.txt",
        "LPT1",
        "lpt9.log",
        "LPT²",
        "lpt³.log",
        "a" * (MAX_PORTABLE_BASENAME_UTF8_BYTES + 1),
        "é" * 128,
        "\ud800",
    ],
)
def test_strict_portable_basename_rejects_nonportable_names(name: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        validate_portable_basename(name, "filename")


def test_strict_portable_schema_caps_components_by_code_points() -> None:
    assert re.fullmatch(PORTABLE_BASENAME_SCHEMA_PATTERN, "a" * 255) is not None
    assert re.fullmatch(PORTABLE_BASENAME_SCHEMA_PATTERN, "a" * 256) is None
    assert re.fullmatch(ASSET_KEY_SCHEMA_PATTERN, f"{'a' * 255}/return.bin") is not None
    assert re.fullmatch(ASSET_KEY_SCHEMA_PATTERN, f"{'a' * 256}/return.bin") is None


@pytest.mark.parametrize(
    "name",
    [
        "report?.py",
        "NUL.py",
        "report. ",
        "folder\\report.py",
        "\x01report.py",
        " report.py ",
    ],
)
def test_notebook_basename_preserves_active_source_names(name: str) -> None:
    assert validate_notebook_basename(name, "notebook.filename") == name
    assert re.fullmatch(NOTEBOOK_BASENAME_SCHEMA_PATTERN, name) is not None


@pytest.mark.parametrize("name", ["", "folder/report.py", "report\x00.py", "\ud800"])
def test_notebook_basename_rejects_non_basenames(name: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        validate_notebook_basename(name, "notebook.filename")


def test_asset_key_returns_portable_components() -> None:
    key = "project/cache/return.bin"

    assert validate_asset_key(key, "asset.key") == key
    assert asset_key_components(key, "asset.key") == ("project", "cache", "return.bin")
    assert re.fullmatch(ASSET_KEY_SCHEMA_PATTERN, key) is not None


def test_asset_key_caps_components_and_total_key_by_utf8_bytes() -> None:
    boundary_component = "é" * 127
    valid_key = "/".join(
        [
            "a" * 255,
            "b" * 255,
            "c" * 255,
            "d",
            "e" * 250 + ".bin",
        ]
    )

    assert len(boundary_component.encode("utf-8")) == 254
    assert validate_asset_key(f"{boundary_component}/return.bin", "asset.key")
    assert len(valid_key.encode("utf-8")) == MAX_ASSET_KEY_UTF8_BYTES
    assert validate_asset_key(valid_key, "asset.key") == valid_key

    with pytest.raises(ValueError):
        validate_asset_key(f"{'é' * 128}/return.bin", "asset.key")
    with pytest.raises(ValueError):
        validate_asset_key(valid_key.replace("e" * 250, "e" * 251), "asset.key")


@pytest.mark.parametrize(
    "key",
    [
        " project/return.bin",
        "project/return.bin ",
        "project/return.json",
        "project//return.bin",
        "project/../return.bin",
        "project/stream:name/return.bin",
        "project/trailing./return.bin",
        "project/CON/return.bin",
        "project/CONIN$/return.bin",
        "project/conout$.txt/return.bin",
        "project/lpt9.txt/return.bin",
        "project/COM¹/return.bin",
        "project/lpt³.txt/return.bin",
        "project/\ud800/return.bin",
    ],
)
def test_asset_key_rejects_nonportable_components(key: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        validate_asset_key(key, "asset.key")
