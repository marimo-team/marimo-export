"""Write resolved export requests into portable static export bundles."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from moexport.blobs import ContentAddressedBlobStore
from moexport.bundle.indexes import (
    root_href,
    update_invocation_index,
    update_root_index,
)
from moexport.bundle.materialize import materialize_scenarios
from moexport.bundle.records import (
    bundle_identity,
    core_manifest,
    invocation_record,
    notebook_record,
    source_spec_record,
)
from moexport.bundle.schema import (
    BUNDLE_SCHEMA,
    BUNDLE_VERSION,
    BundleManifest,
    InvocationRecord,
)
from moexport.evaluate import EvaluateResult
from moexport.jsonio import write_json
from moexport.request import ResolvedExportRequest


class BundleWriteResult:
    """Files and manifest produced by one bundle write."""

    __slots__ = (
        "bundle_path",
        "invocation",
        "invocation_index_path",
        "invocation_path",
        "manifest",
        "manifest_path",
    )

    def __init__(
        self,
        *,
        bundle_path: Path,
        manifest_path: Path,
        manifest: dict[str, Any],
        invocation_path: Path,
        invocation_index_path: Path,
        invocation: dict[str, Any],
    ) -> None:
        self.bundle_path = bundle_path
        self.manifest_path = manifest_path
        self.manifest = manifest
        self.invocation_path = invocation_path
        self.invocation_index_path = invocation_index_path
        self.invocation = invocation


async def write_bundle(
    request: ResolvedExportRequest,
    evaluation: EvaluateResult,
) -> BundleWriteResult:
    """Write one manifest-only bundle plus shared content-addressed blobs."""

    request.output_root.mkdir(parents=True, exist_ok=True)
    staging_path = request.output_root / f".tmp-{uuid.uuid4().hex}"
    staging_path.mkdir(parents=True)

    try:
        blob_store = ContentAddressedBlobStore(
            request.blob_base_path,
            href_prefix=request.blob_href_prefix,
        )
        notebook = notebook_record(request.notebook_source, blob_store)
        materialized = await materialize_scenarios(
            request=request,
            blob_store=blob_store,
            evaluation=evaluation,
        )
        core = core_manifest(
            request=request,
            notebook=notebook,
            scenarios=materialized.manifest,
        )
        source_spec = source_spec_record(request)
        identity = bundle_identity(core)
        final_path = request.output_root / "bundles" / identity.id
        manifest = {
            "schema": BUNDLE_SCHEMA,
            "version": BUNDLE_VERSION,
            "id": identity.id,
            "sha256": identity.sha256,
            **core,
            "provenance": {
                "invocations_index_href": root_href(
                    request.output_root,
                    final_path / "traces" / "index.json",
                ),
                "source_spec_sha256": source_spec["sha256"],
                "source_spec": source_spec["spec"],
            },
        }
        BundleManifest.model_validate(manifest)
        write_json(staging_path / "manifest.json", manifest)

        if final_path.exists():
            write_json(final_path / "manifest.json", manifest)
        else:
            final_path.parent.mkdir(parents=True, exist_ok=True)
            staging_path.rename(final_path)

        invocation = invocation_record(
            request=request,
            identity=identity,
            notebook=notebook,
            source_spec=source_spec,
            traces=materialized.traces,
            evaluation=evaluation,
        )
        InvocationRecord.model_validate(invocation)
        invocation_path = final_path / "traces" / f"{invocation['id']}.json"
        write_json(invocation_path, invocation)
        invocation_index_path = update_invocation_index(
            root=request.output_root,
            bundle_path=final_path,
            identity=identity,
            invocation=invocation,
        )
        update_root_index(
            root=request.output_root,
            identity=identity,
            manifest_path=final_path / "manifest.json",
            invocation=invocation,
        )

        return BundleWriteResult(
            bundle_path=final_path,
            manifest_path=final_path / "manifest.json",
            manifest=manifest,
            invocation_path=invocation_path,
            invocation_index_path=invocation_index_path,
            invocation=invocation,
        )
    finally:
        if staging_path.exists():
            shutil.rmtree(staging_path)
