"""Maintain bundle invocation indexes and export-root indexes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from moexport.bundle.records import BundleIdentity
from moexport.bundle.schema import (
    BUNDLE_VERSION,
    INVOCATION_INDEX_SCHEMA,
    ROOT_INDEX_SCHEMA,
    BundleReference,
    InvocationIndex,
    InvocationSummary,
    RootBundleSummary,
    RootIndex,
)
from moexport.jsonio import write_json


def update_invocation_index(
    *,
    root: Path,
    bundle_path: Path,
    identity: BundleIdentity,
    invocation: dict[str, Any],
) -> Path:
    index_path = bundle_path / "traces" / "index.json"
    invocation_summary = InvocationSummary(
        id=invocation["id"],
        sha256=invocation["sha256"],
        created_at=invocation["created_at"],
        href=root_href(root, bundle_path / "traces" / f"{invocation['id']}.json"),
    )
    if index_path.exists():
        index = InvocationIndex.model_validate(read_json(index_path))
    else:
        index = InvocationIndex(
            schema=INVOCATION_INDEX_SCHEMA,
            version=BUNDLE_VERSION,
            bundle=BundleReference(
                id=identity.id,
                sha256=identity.sha256,
                manifest_href=root_href(root, bundle_path / "manifest.json"),
            ),
            invocations=[],
        )

    invocations = [item for item in index.invocations if item.id != invocation["id"]]
    invocations.append(invocation_summary)
    invocations.sort(key=lambda item: item.created_at)

    index = InvocationIndex(
        schema=INVOCATION_INDEX_SCHEMA,
        version=BUNDLE_VERSION,
        bundle=index.bundle,
        invocations=invocations,
    )
    write_json(index_path, index.model_dump(mode="json", by_alias=True))
    return index_path


def update_root_index(
    *,
    root: Path,
    identity: BundleIdentity,
    manifest_path: Path,
    invocation: dict[str, Any],
) -> Path:
    index_path = root / "index.json"
    bundle_summary = RootBundleSummary(
        id=identity.id,
        sha256=identity.sha256,
        manifest_href=root_href(root, manifest_path),
        updated_at=invocation["created_at"],
        latest_invocation_href=root_href(
            root,
            manifest_path.parent / "traces" / f"{invocation['id']}.json",
        ),
    )
    if index_path.exists():
        root_index = RootIndex.model_validate(read_json(index_path))
    else:
        root_index = RootIndex(
            schema=ROOT_INDEX_SCHEMA,
            version=BUNDLE_VERSION,
            latest=None,
            bundles=[],
        )

    bundles = [item for item in root_index.bundles if item.id != identity.id]
    bundles.append(bundle_summary)
    bundles.sort(key=lambda item: item.updated_at)
    root_index = RootIndex(
        schema=ROOT_INDEX_SCHEMA,
        version=BUNDLE_VERSION,
        latest=bundle_summary,
        bundles=bundles,
    )

    write_json(index_path, root_index.model_dump(mode="json", by_alias=True))
    return index_path


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def root_href(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()
