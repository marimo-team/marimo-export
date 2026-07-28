from __future__ import annotations

from marimo_export._marimo.compat import require_capabilities


def test_attached_marimo_exposes_live_capture_capabilities() -> None:
    report = require_capabilities()

    assert report.version
    assert report.names == (
        "asset_transfer",
        "blob_asset",
        "cache_cells",
        "cell_cache_receipts",
        "child_sessions",
        "child_ui_updates",
        "code_mode_projection_cells",
        "definition_overrides",
        "setup_definition_overrides",
    )
