"""Deterministic identity for one installed marimo-export implementation."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def implementation_identity() -> str:
    """Hash the installed Python sources that implement the bridge contract."""

    root = Path(__file__).parent
    digest = hashlib.sha256()
    for source in sorted(root.rglob("*.py")):
        relative = source.relative_to(root).as_posix().encode("utf-8")
        payload = source.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


__all__ = ["implementation_identity"]
