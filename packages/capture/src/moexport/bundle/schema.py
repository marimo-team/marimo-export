"""Pydantic models for static export bundle JSON files."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from moexport.artifacts import ArtifactData
from moexport.blobs import BlobRef, BundleHref

BUNDLE_SCHEMA = "moexport.bundle.v1"
INVOCATION_SCHEMA = "moexport.invocation.v1"
INVOCATION_INDEX_SCHEMA = "moexport.invocation_index.v1"
ROOT_INDEX_SCHEMA = "moexport.root_index.v1"
BUNDLE_VERSION = 1

JsonObject = dict[str, Any]
ModelT = TypeVar("ModelT", bound=BaseModel)


class BundleSchemaModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class NotebookRecord(BundleSchemaModel):
    name: str | None
    source: BlobRef | None
    source_sha256: str | None = None


class IdentityRecord(BundleSchemaModel):
    id: str
    sha256: str


class CaptureRecord(BundleSchemaModel):
    id: str
    request_sha256: str


class ManifestValue(BundleSchemaModel):
    source: JsonObject
    artifacts: list[str]


class ManifestArtifact(BundleSchemaModel):
    """Manifest form of `Artifact`.

    `format_id` identifies the portable payload format. The authored artifact
    name lives in the containing value map.
    """

    format_id: str
    media_type: str | None
    data: ArtifactData
    metadata: JsonObject | None


class ManifestScenario(BundleSchemaModel):
    id: str
    state: JsonObject = Field(default_factory=dict)
    declared_state: JsonObject | None = None
    values: dict[str, dict[str, ManifestArtifact]]


class ProvenanceRecord(BundleSchemaModel):
    invocations_index_href: BundleHref | None = None
    source_spec_sha256: str | None = None
    source_spec: JsonObject | None = None


class BundleManifest(BundleSchemaModel):
    schema_: Literal["moexport.bundle.v1"] = Field(alias="schema")
    version: int
    id: str
    sha256: str
    notebook: NotebookRecord
    scenario_set: IdentityRecord
    capture: CaptureRecord
    values: dict[str, ManifestValue]
    scenarios: list[ManifestScenario]
    provenance: ProvenanceRecord = Field(default_factory=ProvenanceRecord)


class BundleReference(BundleSchemaModel):
    id: str
    sha256: str
    manifest_href: BundleHref


class InvocationSummary(BundleSchemaModel):
    id: str
    sha256: str
    created_at: str
    href: BundleHref


class InvocationIndex(BundleSchemaModel):
    schema_: Literal["moexport.invocation_index.v1"] = Field(alias="schema")
    version: int
    bundle: BundleReference
    invocations: list[InvocationSummary]


class InvocationScenario(BundleSchemaModel):
    id: str
    state: JsonObject = Field(default_factory=dict)
    declared_state: JsonObject | None = None
    trace: JsonObject = Field(default_factory=dict)


class InvocationRecord(BundleSchemaModel):
    schema_: Literal["moexport.invocation.v1"] = Field(alias="schema")
    version: int
    id: str
    sha256: str
    created_at: str
    bundle: BundleReference
    notebook: NotebookRecord
    scenario_set: IdentityRecord
    capture: CaptureRecord
    source_spec: JsonObject
    scenarios: list[InvocationScenario]
    evaluation: JsonObject


class RootBundleSummary(BundleSchemaModel):
    id: str
    sha256: str
    manifest_href: BundleHref
    updated_at: str
    latest_invocation_href: BundleHref


class RootIndex(BundleSchemaModel):
    schema_: Literal["moexport.root_index.v1"] = Field(alias="schema")
    version: int
    latest: RootBundleSummary | None
    bundles: list[RootBundleSummary]


def read_model(path: Path, model: type[ModelT]) -> ModelT:
    return model.model_validate_json(path.read_text(encoding="utf-8"))


__all__ = [
    "BUNDLE_SCHEMA",
    "BUNDLE_VERSION",
    "INVOCATION_INDEX_SCHEMA",
    "INVOCATION_SCHEMA",
    "ROOT_INDEX_SCHEMA",
    "BundleManifest",
    "BundleReference",
    "BundleSchemaModel",
    "CaptureRecord",
    "IdentityRecord",
    "InvocationIndex",
    "InvocationRecord",
    "InvocationScenario",
    "InvocationSummary",
    "JsonObject",
    "ManifestArtifact",
    "ManifestScenario",
    "ManifestValue",
    "NotebookRecord",
    "ProvenanceRecord",
    "RootBundleSummary",
    "RootIndex",
    "read_model",
]
