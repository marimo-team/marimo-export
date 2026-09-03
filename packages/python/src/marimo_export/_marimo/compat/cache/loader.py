"""Run native lazy-cache deserializers on the kernel thread."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from marimo._save.loaders.lazy import LazyLoader

if TYPE_CHECKING:
    from marimo._save.signing import CacheSigner


class SequentialLazyLoader(LazyLoader):
    """Preserve Marimo's cache format while moving decoder execution."""

    def _read_blobs(
        self,
        unique_keys: set[str],
        ref_type_hints: dict[str, str | None],
        return_ref: str | None,
        return_type_hint: str | None,
        blob_hash_map: dict[str, str] | None = None,
        effective_signer: CacheSigner | None = None,
    ) -> dict[str, Any]:
        from marimo._save.loaders.lazy import (
            LOGGER,
            CacheSignatureError,
            _incomplete_cache_error,
        )

        unpickled: dict[str, Any] = {}
        signature_errors: list[CacheSignatureError] = []
        missing = False
        unreadable = False
        for key in unique_keys:
            try:
                data = self.store.get(key)
                if data:
                    unpickled[key] = self._deserialize_blob(
                        key,
                        data,
                        ref_type_hints,
                        return_ref,
                        return_type_hint,
                        blob_hash_map,
                        effective_signer,
                    )
                else:
                    missing = True
            except CacheSignatureError as error:
                signature_errors.append(error)
                missing = True
            except Exception as error:
                LOGGER.warning("Failed to deserialize blob %s: %s", key, error)
                unreadable = True
        if signature_errors:
            raise signature_errors[0]
        if missing:
            raise _incomplete_cache_error(effective_signer)
        if unreadable:
            raise FileNotFoundError("Incomplete cache: a blob could not be deserialized")
        return unpickled


__all__ = ["SequentialLazyLoader"]
