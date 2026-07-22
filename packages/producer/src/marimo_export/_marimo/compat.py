from __future__ import annotations

from marimo_export import __version__
from marimo_export.errors import UnsupportedMarimoError

SUPPORTED_MARIMO = "0.23.14"
MARIMO_ADAPTER = f"marimo-{SUPPORTED_MARIMO}"


def require_supported_marimo() -> str:
    import marimo

    version = marimo.__version__
    if version != SUPPORTED_MARIMO:
        raise UnsupportedMarimoError(
            f"marimo-export {__version__} requires marimo {SUPPORTED_MARIMO}, found {version}"
        )
    return version
