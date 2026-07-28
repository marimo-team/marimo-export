"""Validate the installed Python distribution and marimo dependency."""

from __future__ import annotations

import json
from importlib import metadata

_MARIMO_URL = "https://github.com/peter-gy/marimo.git"
_MARIMO_COMMIT = "0f5fd5d55b4d65d06a814842af3228f57c8ae9c8"
_MARIMO_REQUIREMENT = f"marimo @ git+{_MARIMO_URL}@{_MARIMO_COMMIT}"


def main() -> None:
    requirements = metadata.requires("marimo-export") or []
    if _MARIMO_REQUIREMENT not in requirements:
        raise RuntimeError("marimo-export wheel must require the pinned marimo Git revision")

    direct_url_text = metadata.distribution("marimo").read_text("direct_url.json")
    if direct_url_text is None:
        raise RuntimeError("installed marimo distribution must record its Git origin")
    direct_url = json.loads(direct_url_text)
    vcs_info = direct_url.get("vcs_info", {})
    if (
        direct_url.get("url") != _MARIMO_URL
        or vcs_info.get("vcs") != "git"
        or vcs_info.get("commit_id") != _MARIMO_COMMIT
        or vcs_info.get("requested_revision") != _MARIMO_COMMIT
    ):
        raise RuntimeError("installed marimo distribution must resolve the pinned Git revision")

    from marimo._save.stubs import BlobAsset

    if BlobAsset.__name__ != "BlobAsset":
        raise RuntimeError("installed marimo distribution must expose BlobAsset")

    import marimo_export

    if not marimo_export.__all__:
        raise RuntimeError("installed marimo-export distribution must expose its public API")


if __name__ == "__main__":
    main()
