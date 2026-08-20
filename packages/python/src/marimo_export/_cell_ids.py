"""Normalize Marimo cell identifiers at adapter boundaries."""

from __future__ import annotations

from uuid import UUID

_MAX_CELL_ID_BYTES = 1_024
_UUID_LENGTH = 36


def canonical_cell_id(value: object) -> str:
    """Remove Marimo's external UUIDv4 scope from one native cell ID."""

    cell_id = str(value)
    if not cell_id or len(cell_id.encode("utf-8")) > _MAX_CELL_ID_BYTES:
        raise ValueError("cell ID must be a bounded non-empty string")
    if len(cell_id) <= _UUID_LENGTH:
        return cell_id
    prefix = cell_id[:_UUID_LENGTH]
    suffix = cell_id[_UUID_LENGTH:]
    try:
        external = UUID(prefix, version=4)
    except ValueError:
        return cell_id
    return suffix if suffix and str(external) == prefix else cell_id


__all__ = ["canonical_cell_id"]
