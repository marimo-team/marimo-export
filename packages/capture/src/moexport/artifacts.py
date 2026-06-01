"""Portable artifact descriptors returned by exporter callables."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from moexport.blobs import BlobRef

type JsonPrimitive = str | int | float | bool | None
type JsonValue = JsonPrimitive | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


class ArtifactData(BaseModel):
    """Artifact payload stored as one or more content-addressed blob files."""

    model_config = ConfigDict(frozen=True)

    type: Literal["bundle"] = Field(
        default="bundle",
        description="Discriminator for artifact data backed by external blob files.",
    )
    files: dict[str, BlobRef] = Field(
        description="Named files that make up this representation.",
    )
    entry: str | None = Field(
        description="Primary file key in `files`, or null when there is no single entry.",
    )


class Artifact(BaseModel):
    """Exporter-produced artifact descriptor.

    The kernel export runner owns provenance and manifest placement. Exporters
    only describe the portable representation they produced.
    """

    model_config = ConfigDict(frozen=True)

    format: str = Field(
        description="Stable format identifier, for example `dataframe.arrow.v1`.",
    )
    media_type: str | None = Field(
        description="Top-level MIME type for the representation, when there is one.",
    )
    data: ArtifactData = Field(
        description=(
            "Portable payload descriptor. Inline artifact payloads are "
            "intentionally disallowed so every value participates in "
            "content-addressed dedupe."
        ),
    )
    metadata: JsonObject | None = Field(
        description="Small JSON-shaped facts for indexing, inspection, or loader hints.",
    )
