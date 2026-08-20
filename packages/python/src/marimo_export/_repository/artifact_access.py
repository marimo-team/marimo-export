from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import cast

from marimo_export._json import JsonValue, canonical_bytes, sha256_bytes
from marimo_export._repository.artifact_context import ArtifactContext
from marimo_export._repository.files import verify_prepared_state
from marimo_export._repository.handles import PreparedExportArtifact, PreparedState
from marimo_export._repository.models import (
    ExportGenerationRecord,
    PreparedStateRecord,
    RepositoryIdentity,
    RepositoryIntegrityError,
    digest,
)
from marimo_export._repository.paths import export_path
from marimo_export._repository.sqlite.records import GenerationRow, StateRow
from marimo_export.errors import NotebookExportError
from marimo_export.wire import parse_canonical_json


def lookup_prepared_states(
    context: ArtifactContext,
    *,
    producer_sha256: str,
    output_plan_sha256: str,
    state_fingerprints: Sequence[str],
) -> tuple[Mapping[str, PreparedState], bool]:
    requested = tuple(dict.fromkeys(state_fingerprints))
    expiry = context.leases.expiry()
    rows = context.catalog.current_states(
        producer_sha256=producer_sha256,
        output_plan_sha256=output_plan_sha256,
        state_fingerprints=requested,
        owner=context.leases.owner,
        expires_at_us=expiry,
        now_us=_now_us(),
    )
    opened: dict[str, PreparedState] = {}
    corrupt = False
    for row in rows:
        handle = open_state(context, row, expiry)
        if handle is None:
            corrupt = True
        else:
            opened[row.state_fingerprint] = handle
    return MappingProxyType(opened), corrupt


def current(
    context: ArtifactContext,
    identity: RepositoryIdentity,
) -> tuple[PreparedExportArtifact | None, bool]:
    expiry = context.leases.expiry()
    row = context.catalog.current_generation(
        identity,
        owner=context.leases.owner,
        expires_at_us=expiry,
        now_us=_now_us(),
    )
    handle = open_generation(context, row, expiry)
    return handle, row is not None and handle is None


def generation(
    context: ArtifactContext,
    identity: RepositoryIdentity,
    instance: str,
) -> tuple[PreparedExportArtifact | None, bool]:
    expiry = context.leases.expiry()
    row = context.catalog.generation(
        identity,
        instance,
        owner=context.leases.owner,
        expires_at_us=expiry,
        now_us=_now_us(),
    )
    handle = open_generation(context, row, expiry)
    return handle, row is not None and handle is None


def open_state(
    context: ArtifactContext,
    row: StateRow,
    expiry: int,
) -> PreparedState | None:
    try:
        manifest = verify_prepared_state(context.state_path(row))
        if (
            manifest.instance != row.instance
            or manifest.producer_sha256 != row.producer_sha256
            or manifest.output_plan_sha256 != row.output_plan_sha256
            or manifest.state_fingerprint != row.state_fingerprint
            or state_key_for(
                manifest.producer_sha256,
                manifest.output_plan_sha256,
                manifest.state_fingerprint,
            )
            != row.state_key
            or canonical_bytes(manifest.metadata) != row.metadata
            or row.metadata_bytes != len(row.metadata)
            or manifest.closure.content_bytes != row.content_bytes
        ):
            raise RepositoryIntegrityError("Prepared state catalog metadata is stale.")
        return state_handle(context, row, manifest.closure.files, expiry)
    except (NotebookExportError, RepositoryIntegrityError):
        return None


def state_handle(
    context: ArtifactContext,
    row: StateRow,
    files: frozenset[str],
    expiry: int,
) -> PreparedState:
    try:
        metadata = parse_canonical_json(row.metadata, "prepared state metadata")
        if not isinstance(metadata, dict):
            raise TypeError("metadata")
    except (TypeError, ValueError) as error:
        raise RepositoryIntegrityError("Prepared state metadata is invalid.") from error
    record = PreparedStateRecord(
        row.producer_sha256,
        row.output_plan_sha256,
        row.state_fingerprint,
        row.instance,
        context.state_path(row),
        cast(dict[str, JsonValue], metadata),
        files,
        row.content_bytes,
    )
    lease = context.leases.acquire(
        ("state", row.state_key, row.instance),
        renewed_until_us=expiry,
    )
    return PreparedState(record, lease)


def open_generation(
    context: ArtifactContext,
    row: GenerationRow | None,
    expiry: int,
) -> PreparedExportArtifact | None:
    if row is None:
        return None
    try:
        path = export_path(context.root, row.identity, row.instance)
        instance, closure = context.verify_export(path)
        generation_state_fingerprints(row.metadata, row.instance)
        if (
            instance != row.instance
            or row.metadata_bytes != len(row.metadata)
            or closure.content_bytes != row.content_bytes
        ):
            raise RepositoryIntegrityError("Prepared export catalog metadata is stale.")
        return generation_handle(context, row, closure.files, expiry)
    except (NotebookExportError, RepositoryIntegrityError):
        return None


def generation_handle(
    context: ArtifactContext,
    row: GenerationRow,
    files: frozenset[str],
    expiry: int,
) -> PreparedExportArtifact:
    fingerprints = generation_state_fingerprints(row.metadata, row.instance)
    record = ExportGenerationRecord(
        row.identity,
        row.instance,
        export_path(context.root, row.identity, row.instance),
        fingerprints,
        row.captured_observation_revision,
        row.content_bytes,
    )
    lease = context.leases.acquire(
        ("generation", row.identity_key, row.instance),
        renewed_until_us=expiry,
    )
    return PreparedExportArtifact(record, files, lease)


def state_key_for(
    producer_sha256: str,
    output_plan_sha256: str,
    state_fingerprint: str,
) -> str:
    return sha256_bytes(
        canonical_bytes(
            {
                "output_plan_sha256": output_plan_sha256,
                "producer_sha256": producer_sha256,
                "state_fingerprint": state_fingerprint,
            }
        )
    )


def generation_state_fingerprints(metadata: bytes, instance: str) -> tuple[str, ...]:
    try:
        raw = parse_canonical_json(metadata, "prepared export metadata")
        if not isinstance(raw, dict) or set(raw) != {
            "schema",
            "instance",
            "state_fingerprints",
            "state_instances",
        }:
            raise ValueError("fields")
        if raw["schema"] != "marimo-export.repository-export.v1" or raw["instance"] != instance:
            raise ValueError("identity")
        fingerprints = raw["state_fingerprints"]
        instances = raw["state_instances"]
        if not isinstance(fingerprints, list) or not isinstance(instances, dict):
            raise TypeError("states")
        parsed = tuple(cast(list[str], fingerprints))
        if parsed != tuple(sorted(set(parsed))) or set(instances) != set(parsed):
            raise ValueError("membership")
        for fingerprint in parsed:
            digest(fingerprint, "prepared export state fingerprint")
            digest(instances[fingerprint], "prepared state instance")
        return parsed
    except (TypeError, ValueError) as error:
        raise RepositoryIntegrityError("Prepared export metadata is invalid.") from error


def _now_us() -> int:
    import time

    return time.time_ns() // 1000


__all__ = [
    "current",
    "generation",
    "generation_handle",
    "generation_state_fingerprints",
    "lookup_prepared_states",
    "open_generation",
    "open_state",
    "state_handle",
    "state_key_for",
]
