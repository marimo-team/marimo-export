from __future__ import annotations

from marimo_export._marimo.compat import require_capabilities


def test_attached_marimo_exposes_live_capture_capabilities() -> None:
    report = require_capabilities()

    assert report.version
    assert report.names == (
        "blob-asset",
        "code-mode",
        "lazy-cache-receipt",
        "virtual-file-transfer",
    )
