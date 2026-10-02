"""Composition boundary for native Marimo blob values."""

from __future__ import annotations


def to_native_blob_asset(value: object) -> object:
    from marimo_export._marimo.compat.blob import to_native_blob_asset as convert

    return convert(value)


__all__ = ["to_native_blob_asset"]
