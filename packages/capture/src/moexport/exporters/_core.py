"""Core exporter interfaces and artifact-writing context."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from moexport.artifacts import Artifact, ArtifactData, JsonObject
from moexport.blobs import BlobContent, BlobRef, ContentAddressedBlobStore


class ExporterContext(Protocol):
    """Minimal artifact-writing API available to exporter callables."""

    scenario_id: str
    value_name: str
    artifact_name: str

    def write_blob(
        self,
        name: str,
        data: BlobContent,
        *,
        media_type: str | None = None,
    ) -> BlobRef:
        """Write bytes and return their content-addressed reference."""
        raise NotImplementedError()

    def artifact(
        self,
        *,
        format_id: str,
        files: dict[str, BlobRef],
        entry: str | None = None,
        media_type: str | None = None,
        metadata: JsonObject | None = None,
    ) -> Artifact:
        """Wrap blob refs in the standard bundle-backed artifact envelope."""
        raise NotImplementedError()


class BundleExporterContext:
    """Exporter context backed by a content-addressed bundle blob store."""

    def __init__(
        self,
        *,
        scenario_id: str,
        value_name: str,
        artifact_name: str,
        blob_store: ContentAddressedBlobStore,
    ) -> None:
        self.scenario_id = scenario_id
        self.value_name = value_name
        self.artifact_name = artifact_name
        self.blob_store = blob_store

    def write_blob(
        self,
        name: str,
        data: BlobContent,
        *,
        media_type: str | None = None,
    ) -> BlobRef:
        """Write bytes through the bundle's deduping blob store."""

        return self.blob_store.write(name, data, media_type=media_type)

    def artifact(
        self,
        *,
        format_id: str,
        files: dict[str, BlobRef],
        entry: str | None = None,
        media_type: str | None = None,
        metadata: JsonObject | None = None,
    ) -> Artifact:
        """Create the standard artifact object returned by exporters."""

        return Artifact(
            format_id=format_id,
            media_type=media_type,
            data=ArtifactData(files=files, entry=entry),
            metadata=metadata,
        )


class ExporterOptions(BaseModel):
    """Base model for exporter option objects.

    Config files should not silently accept misspelled exporter options.
    """

    model_config = ConfigDict(extra="forbid")


class MissingOptionalDependency(ImportError):
    """Raised when an exporter extra is required but not installed."""

    def __init__(self, *, package: str, extra: str, purpose: str) -> None:
        self.package = package
        self.extra = extra
        self.purpose = purpose
        super().__init__(
            f"{purpose} requires {package!r}. "
            f"Install it with `pip install 'moexport[{extra}]'`."
        )


class Exporter(Protocol):
    """Callable shape implemented by all Python exporters."""

    def __call__(
        self,
        value: Any,
        ctx: ExporterContext,
        **options: Any,
    ) -> Artifact: ...
