from __future__ import annotations

from typing import Any, cast

import marimo._save.loaders.lazy as lazy_module
import marimo._save.stubs as stubs_module
import marimo._save.stubs.lazy_stub as schema_module
import msgspec
import pytest
from marimo._save.loaders.lazy import LazyLoader
from marimo_export._marimo.compat.cache.probe import require_cache_capabilities
from marimo_export.errors import CompatibilityError


@pytest.mark.parametrize(
    ("owner", "attribute", "symbol"),
    (
        (LazyLoader, "_effective_mode", "LazyLoader._effective_mode"),
        (
            LazyLoader,
            "_resolve_effective_signer",
            "LazyLoader._resolve_effective_signer",
        ),
        (lazy_module, "_verify_signed_blob", "_verify_signed_blob"),
        (lazy_module, "from_item", "from_item"),
    ),
)
def test_receipt_trust_symbol_drift_is_classified(
    monkeypatch: pytest.MonkeyPatch,
    owner: object,
    attribute: str,
    symbol: str,
) -> None:
    monkeypatch.setattr(owner, attribute, lambda *args, **kwargs: None)

    with pytest.raises(CompatibilityError) as raised:
        require_cache_capabilities()

    assert raised.value.code == "marimo_incompatible"
    assert symbol in _symbols(raised.value)


@pytest.mark.parametrize(
    ("owner", "attribute", "symbol"),
    (
        (stubs_module, "BlobAsset", "BlobAsset.schema"),
        (schema_module, "Cache", "Cache.schema"),
        (schema_module, "Item", "Item.schema"),
        (schema_module, "Meta", "Meta.schema"),
    ),
)
def test_receipt_schema_drift_is_classified(
    monkeypatch: pytest.MonkeyPatch,
    owner: object,
    attribute: str,
    symbol: str,
) -> None:
    monkeypatch.setattr(owner, attribute, object)

    with pytest.raises(CompatibilityError) as raised:
        require_cache_capabilities()

    assert raised.value.code == "marimo_incompatible"
    assert symbol in _symbols(raised.value)


def test_receipt_signature_error_type_drift_is_classified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lazy_module, "CacheSignatureError", object())

    with pytest.raises(CompatibilityError) as raised:
        require_cache_capabilities()

    assert raised.value.code == "marimo_incompatible"
    assert "CacheSignatureError" in _symbols(raised.value)


@pytest.mark.parametrize(
    ("owner", "attribute", "symbol"),
    (
        (stubs_module, "BlobAsset", "BlobAsset.schema_source"),
        (schema_module, "Cache", "Cache.schema_source"),
        (schema_module, "Item", "Item.schema_source"),
        (schema_module, "Meta", "Meta.schema_source"),
    ),
)
def test_receipt_schema_type_drift_is_classified(
    monkeypatch: pytest.MonkeyPatch,
    owner: object,
    attribute: str,
    symbol: str,
) -> None:
    original = getattr(owner, attribute)
    replacement = msgspec.defstruct(
        attribute,
        [(field.name, Any, None) for field in msgspec.structs.fields(original)],
    )
    monkeypatch.setattr(owner, attribute, replacement)

    with pytest.raises(CompatibilityError) as raised:
        require_cache_capabilities()

    assert raised.value.code == "marimo_incompatible"
    assert symbol in _symbols(raised.value)


def test_receipt_signature_error_identity_drift_is_classified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ForeignSignatureError(Exception):
        pass

    monkeypatch.setattr(lazy_module, "CacheSignatureError", ForeignSignatureError)

    with pytest.raises(CompatibilityError) as raised:
        require_cache_capabilities()

    assert raised.value.code == "marimo_incompatible"
    assert "CacheSignatureError" in _symbols(raised.value)


def _symbols(error: CompatibilityError) -> list[str]:
    return cast(list[str], error.details.get("symbols", []))
