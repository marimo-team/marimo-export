"""Producer and local-reader bounds for one notebook export."""

MAX_EXPORT_ASSET_BYTES = 64 * 1024 * 1024
MAX_EXPORT_CLOSURE_BYTES = 512 * 1024 * 1024

__all__ = ["MAX_EXPORT_ASSET_BYTES", "MAX_EXPORT_CLOSURE_BYTES"]
