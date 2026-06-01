"""Bundle manifest, provenance, and identity records."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from moexport.blobs import ContentAddressedBlobStore
from moexport.bundle.schema import (
    BUNDLE_SCHEMA,
    BUNDLE_VERSION,
    INVOCATION_SCHEMA,
)
from moexport.evaluate import EvaluateResult
from moexport.jsonio import sha256_json
from moexport.request import NotebookSource, ResolvedExportRequest
from moexport.sources import source_record

NOTEBOOK_SOURCE_MEDIA_TYPE = "text/x-python"


class BundleIdentity:
    """Stable id for one materialized artifact set."""

    __slots__ = ("id", "sha256")

    def __init__(self, *, id: str, sha256: str) -> None:
        self.id = id
        self.sha256 = sha256


def core_manifest(
    *,
    request: ResolvedExportRequest,
    notebook: dict[str, Any],
    scenarios: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "notebook": notebook,
        "scenario_set": {
            "id": request.scenario_set_identity.id,
            "sha256": request.scenario_set_identity.sha256,
        },
        "capture": {
            "id": request.export_identity.id,
            "request_sha256": request.export_identity.sha256,
        },
        "values": {
            name: {
                "source": source_record(value.source),
                "formats": list(value.formats),
            }
            for name, value in request.spec.values.items()
        },
        "scenarios": scenarios,
    }


def bundle_identity(core_manifest_record: dict[str, Any]) -> BundleIdentity:
    digest = sha256_json(
        {
            "schema": BUNDLE_SCHEMA,
            "version": BUNDLE_VERSION,
            **core_manifest_record,
        }
    )
    return BundleIdentity(id=f"sha256-{digest[:16]}", sha256=digest)


def trace_record(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "value_preview": result.get("value_preview"),
        **dict(result.get("metadata", {})),
    }


def invocation_record(
    *,
    request: ResolvedExportRequest,
    identity: BundleIdentity,
    notebook: dict[str, Any],
    source_spec: dict[str, Any],
    traces: list[dict[str, Any]],
    evaluation: EvaluateResult,
) -> dict[str, Any]:
    payload = {
        "created_at": utc_now(),
        "bundle": {
            "id": identity.id,
            "sha256": identity.sha256,
            "manifest_href": f"bundles/{identity.id}/manifest.json",
        },
        "notebook": notebook,
        "scenario_set": {
            "id": request.scenario_set_identity.id,
            "sha256": request.scenario_set_identity.sha256,
        },
        "capture": {
            "id": request.export_identity.id,
            "request_sha256": request.export_identity.sha256,
        },
        "source_spec": source_spec,
        "scenarios": traces,
        "evaluation": evaluation["metadata"],
    }
    digest = sha256_json(
        {
            "schema": INVOCATION_SCHEMA,
            "version": BUNDLE_VERSION,
            **payload,
        }
    )
    return {
        "schema": INVOCATION_SCHEMA,
        "version": BUNDLE_VERSION,
        "id": f"sha256-{digest[:16]}",
        "sha256": digest,
        **payload,
    }


def source_spec_record(request: ResolvedExportRequest) -> dict[str, Any]:
    spec = _compact_source_spec(request.spec.model_dump(mode="json", exclude_none=True))
    if request.spec.provenance.spec == "none":
        return {
            "sha256": None,
            "spec": None,
        }
    if request.spec.provenance.spec == "hash":
        return {
            "sha256": sha256_json(spec),
            "spec": None,
        }
    return {
        "sha256": sha256_json(spec),
        "spec": spec,
    }


def _compact_source_spec(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, list):
        return [_compact_source_spec(item) for item in value]

    if isinstance(value, dict):
        result = {
            item_key: _compact_source_spec(item, key=str(item_key))
            for item_key, item in value.items()
            if item is not None
        }
        if key in {"inputs", "options", "ui", "widgets"} and not result:
            return None
        return {item_key: item for item_key, item in result.items() if item is not None}

    return value


def notebook_record(
    notebook_source: NotebookSource,
    blob_store: ContentAddressedBlobStore,
    *,
    source_policy: str = "hash",
) -> dict[str, Any]:
    source = None
    source_sha256 = None if source_policy == "none" else notebook_source.sha256
    if source_policy == "source" and notebook_source.content is not None:
        source_ref = blob_store.write(
            notebook_source.name or "notebook.py",
            notebook_source.content,
            media_type=NOTEBOOK_SOURCE_MEDIA_TYPE,
        )
        if (
            notebook_source.sha256 is not None
            and source_ref.sha256 != notebook_source.sha256
        ):
            raise ValueError("notebook source changed while writing export bundle")
        source = source_ref.model_dump(mode="json")

    return {
        "name": notebook_source.name,
        "source": source,
        "source_sha256": source_sha256,
    }


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
