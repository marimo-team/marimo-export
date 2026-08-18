"""Validate the installed Python distribution and released marimo dependency."""

from __future__ import annotations

from importlib import metadata

_MARIMO_REQUIREMENT = "marimo>=0.23.15,<0.25"


def main() -> None:
    requirements = metadata.requires("marimo-export") or []
    if _MARIMO_REQUIREMENT not in requirements:
        raise RuntimeError(f"marimo-export wheel must require {_MARIMO_REQUIREMENT}")

    import marimo_export

    if not marimo_export.__all__:
        raise RuntimeError("installed marimo-export distribution must expose its public API")
    if marimo_export.BlobAsset.__name__ != "BlobAsset":
        raise RuntimeError("installed marimo-export distribution must expose BlobAsset")

    lifespans = {
        (entry.name, entry.value)
        for entry in metadata.entry_points(group="marimo.kernel.lifespan")
        if entry.dist.name == "marimo-export"
    }
    if lifespans != {("marimo-export", "marimo_export._marimo.entrypoints:kernel_lifespan")}:
        raise RuntimeError("marimo-export wheel must register its managed kernel lifespan")


if __name__ == "__main__":
    main()
