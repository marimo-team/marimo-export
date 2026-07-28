"""Validate the installed Python distribution and released marimo dependency."""

from __future__ import annotations

from importlib import metadata

_MARIMO_REQUIREMENT = "marimo>=0.23.15"


def main() -> None:
    requirements = metadata.requires("marimo-export") or []
    if _MARIMO_REQUIREMENT not in requirements:
        raise RuntimeError("marimo-export wheel must require marimo>=0.23.15")

    from marimo._save.stubs import BlobAsset

    if BlobAsset.__name__ != "BlobAsset":
        raise RuntimeError("installed marimo distribution must expose BlobAsset")

    import marimo_export

    if not marimo_export.__all__:
        raise RuntimeError("installed marimo-export distribution must expose its public API")


if __name__ == "__main__":
    main()
