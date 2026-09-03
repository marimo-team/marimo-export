from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from marimo_export._marimo.compat.cache.probe import (
    MARIMO_VERSION,
    require_cache_capabilities,
)


def test_pinned_marimo_release_matches_the_cache_adapter() -> None:
    require_cache_capabilities()

    root = Path(__file__).parents[3]
    with (root / "packages/python/pyproject.toml").open("rb") as stream:
        package = tomllib.load(stream)

    assert f"marimo=={MARIMO_VERSION}" in package["project"]["dependencies"]
