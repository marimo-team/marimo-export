"""Content-addressed blob storage for exported artifact bytes."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Annotated, TypeAlias

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

BlobContent: TypeAlias = bytes | bytearray | memoryview
BundleHref: TypeAlias = Annotated[
    str,
    AfterValidator(lambda value: validate_bundle_href(value)),
]

BLOB_DIR = "blobs"
HASH_ALGORITHM = "sha256"


def validate_bundle_href(value: str) -> str:
    """Return a canonical bundle-relative href or raise `ValueError`."""

    if not value or value.startswith("/") or "\\" in value:
        raise ValueError(f"invalid bundle href {value!r}")

    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"invalid bundle href {value!r}")

    return "/".join(parts)


class BlobRef(BaseModel):
    """Reference to bytes stored in the export bundle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    href: BundleHref = Field(description="Bundle-relative path to the stored bytes.")
    media_type: str | None = Field(
        description="MIME type of the bytes for this specific artifact reference.",
    )
    size: int = Field(
        ge=0,
        description="Byte length for diagnostics and preload decisions.",
    )
    sha256: str = Field(
        description="Content hash used for dedupe and provenance.",
    )


class ContentAddressedBlobStore:
    """Write bytes once and address them by their SHA-256 digest."""

    def __init__(self, root: str | Path, *, href_prefix: str = BLOB_DIR) -> None:
        self.root = Path(root)
        self.href_prefix = href_prefix.strip("/")

    def write(
        self,
        name: str,
        data: BlobContent,
        *,
        media_type: str | None = None,
    ) -> BlobRef:
        """Write bytes and return a stable blob reference.

        `name` is intentionally not part of the storage path. Exporter-provided
        filenames describe intent. Byte identity is the dedupe key.
        """

        payload = bytes(data)
        digest = hashlib.sha256(payload).hexdigest()
        href = self._href(digest)
        path = self._path(digest)

        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)

        return BlobRef(
            href=href,
            media_type=media_type,
            size=len(payload),
            sha256=digest,
        )

    def _href(self, digest: str) -> str:
        return (
            f"{self.href_prefix}/{HASH_ALGORITHM}/{digest[:2]}/{digest[2:4]}/{digest}"
        )

    def _path(self, digest: str) -> Path:
        return self.root / BLOB_DIR / HASH_ALGORITHM / digest[:2] / digest[2:4] / digest
