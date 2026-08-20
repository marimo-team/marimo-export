"""Validate notebook export delivery destinations."""

from __future__ import annotations

from pathlib import Path

from marimo_export.spec import StrPath


def preflight_export_destination(
    path: StrPath,
    *,
    replace: bool = False,
) -> Path:
    """Validate an export destination and return its absolute path."""

    from marimo_export._writer import preflight_export

    return preflight_export(path, replace=replace)


__all__ = ["preflight_export_destination"]
