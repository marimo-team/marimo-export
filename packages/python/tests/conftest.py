from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@pytest.fixture(autouse=True)
def _isolated_default_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARIMO_EXPORT_REPOSITORY", str(tmp_path / "default-repository"))


@pytest.fixture(autouse=True)
def _isolated_cache_signer(monkeypatch: pytest.MonkeyPatch) -> None:
    # Workers can race while initializing Marimo's shared default key.
    # Each test keeps one signing identity across all of its notebook processes.
    key = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    monkeypatch.setenv("MARIMO_CACHE_SIGNING_PRIVATE_KEY", key.decode("ascii"))
    monkeypatch.delenv("MARIMO_CACHE_SIGNING_PUBLIC_KEY", raising=False)
