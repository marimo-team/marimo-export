from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from marimo_export._diagnostics import record_cleanup_failure
from marimo_export._json import canonical_bytes
from marimo_export._repository.artifact_access import (
    generation_handle,
    state_handle,
    state_key_for,
)
from marimo_export._repository.artifact_context import ArtifactContext
from marimo_export._repository.files import (
    atomic_install,
    discard_quarantine,
    quarantine,
    restore_quarantine,
    verify_prepared_state,
    write_prepared_state_manifest,
)
from marimo_export._repository.handles import (
    PreparedExportArtifact,
    PreparedState,
    StagedExport,
    StagedPreparedState,
)
from marimo_export._repository.models import (
    RepositoryError,
    RepositoryLimitError,
    digest,
)
from marimo_export._repository.paths import export_path, prepared_state_path
from marimo_export._repository.sqlite.records import StateRow
from marimo_export.reader import open_export


def commit_prepared_state(
    context: ArtifactContext,
    staged: StagedPreparedState,
    *,
    metadata: Mapping[str, object],
    replacing_instance: str | None,
    admit: Callable[[int], None],
) -> PreparedState:
    if replacing_instance is not None:
        digest(replacing_instance, "replacing_instance")
    manifest = write_prepared_state_manifest(
        staged.path,
        producer_sha256=staged._producer_sha256,
        output_plan_sha256=staged._output_plan_sha256,
        state_fingerprint=staged._state_fingerprint,
        metadata=metadata,
    )
    if manifest.closure.content_bytes > context.limits.prepared_state_bytes:
        raise RepositoryLimitError("One prepared state exceeds the repository byte limit.")
    admit(manifest.closure.content_bytes)
    state_key = state_key_for(
        staged._producer_sha256,
        staged._output_plan_sha256,
        staged._state_fingerprint,
    )
    target = prepared_state_path(
        context.root,
        staged._producer_sha256,
        staged._output_plan_sha256,
        staged._state_fingerprint,
        manifest.instance,
    )
    context.catalog.check_state_commit(
        state_key=state_key,
        producer_sha256=staged._producer_sha256,
        output_plan_sha256=staged._output_plan_sha256,
        instance=manifest.instance,
        replacing_instance=replacing_instance,
        reservation_owner=staged._reservation.owner,
        reservation_identity_key=staged._reservation.identity_key,
        reservation_fence=staged._reservation.fence,
        reservation_spec_sha256=staged._reservation.identity.spec_sha256,
        now_us=_now_us(),
        timeout_seconds=staged._reservation.operation_timeout_seconds,
    )
    expiry = context.leases.expiry()
    target_preexisting = target.exists() or target.is_symlink()
    try:
        selected_closure = atomic_install(
            staged.path,
            target,
            manifest.instance,
            _state_verifier,
        )
        row = context.catalog.commit_state(
            state_key=state_key,
            producer_sha256=staged._producer_sha256,
            output_plan_sha256=staged._output_plan_sha256,
            state_fingerprint=staged._state_fingerprint,
            instance=manifest.instance,
            metadata=canonical_bytes(manifest.metadata),
            content_bytes=manifest.closure.content_bytes,
            replacing_instance=replacing_instance,
            owner=context.leases.owner,
            expires_at_us=expiry,
            now_us=_now_us(),
            limits=context.limits,
            reservation_owner=staged._reservation.owner,
            reservation_identity_key=staged._reservation.identity_key,
            reservation_fence=staged._reservation.fence,
            reservation_spec_sha256=staged._reservation.identity.spec_sha256,
            timeout_seconds=staged._reservation.operation_timeout_seconds,
        )
    except BaseException as error:
        if not target_preexisting:
            try:
                _retire_uncommitted(
                    context,
                    target,
                    manifest.closure.content_bytes,
                )
            except BaseException as cleanup_error:
                record_cleanup_failure(
                    error,
                    "uncommitted state cleanup",
                    cleanup_error,
                )
        raise
    return state_handle(context, row, selected_closure.files, expiry)


def commit_export(
    context: ArtifactContext,
    staged: StagedExport,
    *,
    states: Sequence[PreparedState],
    captured_observation_revision: int,
    replacing_instance: str | None,
    admit: Callable[[int], None],
    commit_guard: Callable[[], None] | None,
) -> PreparedExportArtifact:
    if not states:
        raise ValueError("states must contain at least one PreparedState")
    if replacing_instance is not None:
        digest(replacing_instance, "replacing_instance")
    state_rows = _state_rows(staged, states)
    instance, closure = context.verify_export(staged.path)
    if closure.content_bytes > context.limits.generation_bytes:
        raise RepositoryLimitError("One prepared export exceeds the repository byte limit.")
    fingerprints = tuple(sorted(row.state_fingerprint for row in state_rows))
    _validate_export_membership(staged, fingerprints)
    metadata = canonical_bytes(
        {
            "schema": "marimo-export.repository-export.v1",
            "instance": instance,
            "state_fingerprints": list(fingerprints),
            "state_instances": {row.state_fingerprint: row.instance for row in state_rows},
        }
    )
    if len(metadata) > context.limits.metadata_bytes:
        raise RepositoryLimitError("Prepared export metadata exceeds its byte limit.")
    admit(closure.content_bytes)
    target = export_path(context.root, staged._identity, instance)
    context.catalog.check_generation_commit(
        identity=staged._identity,
        instance=instance,
        replacing_instance=replacing_instance,
        reservation_owner=staged._reservation.owner,
        reservation_identity_key=staged._reservation.identity_key,
        reservation_fence=staged._reservation.fence,
        now_us=_now_us(),
        timeout_seconds=staged._reservation.operation_timeout_seconds,
    )
    if commit_guard is not None:
        commit_guard()
    expiry = context.leases.expiry()
    target_preexisting = target.exists() or target.is_symlink()
    try:
        selected_closure = atomic_install(
            staged.path,
            target,
            instance,
            context.verify_export,
        )
        row = context.catalog.commit_generation(
            identity=staged._identity,
            instance=instance,
            metadata=metadata,
            captured_observation_revision=captured_observation_revision,
            content_bytes=closure.content_bytes,
            states=state_rows,
            replacing_instance=replacing_instance,
            owner=context.leases.owner,
            expires_at_us=expiry,
            now_us=_now_us(),
            limits=context.limits,
            reservation_owner=staged._reservation.owner,
            reservation_identity_key=staged._reservation.identity_key,
            reservation_fence=staged._reservation.fence,
            timeout_seconds=staged._reservation.operation_timeout_seconds,
        )
    except BaseException as error:
        if not target_preexisting:
            try:
                _retire_uncommitted(context, target, closure.content_bytes)
            except BaseException as cleanup_error:
                record_cleanup_failure(
                    error,
                    "uncommitted export cleanup",
                    cleanup_error,
                )
        raise
    return generation_handle(context, row, selected_closure.files, expiry)


def _state_rows(
    staged: StagedExport,
    states: Sequence[PreparedState],
) -> tuple[StateRow, ...]:
    rows: list[StateRow] = []
    seen: set[str] = set()
    for state in states:
        if not isinstance(state, PreparedState) or not state.alive:
            raise TypeError("states must contain live PreparedState values")
        if (
            state.producer_sha256 != staged._identity.producer_sha256
            or state.output_plan_sha256 != staged._identity.output_plan_sha256
        ):
            raise ValueError("prepared state belongs to another export identity")
        if state.state_fingerprint in seen:
            raise ValueError("states must contain unique fingerprints")
        seen.add(state.state_fingerprint)
        rows.append(
            StateRow(
                state_key_for(
                    state.producer_sha256,
                    state.output_plan_sha256,
                    state.state_fingerprint,
                ),
                state.producer_sha256,
                state.output_plan_sha256,
                state.state_fingerprint,
                state.instance,
                canonical_bytes(state.metadata),
                len(canonical_bytes(state.metadata)),
                state.content_bytes,
            )
        )
    return tuple(rows)


def _validate_export_membership(
    staged: StagedExport,
    fingerprints: tuple[str, ...],
) -> None:
    notebook_export = open_export(staged.path)
    exported = tuple(sorted(state.fingerprint for state in notebook_export.states()))
    if notebook_export.spec_sha256 != staged._identity.spec_sha256:
        raise RepositoryError("Prepared export specification identity is stale.")
    if exported != fingerprints:
        raise RepositoryError("Prepared export state membership is stale.")


def _state_verifier(path: Path):
    manifest = verify_prepared_state(path)
    return manifest.instance, manifest.closure


def _retire_uncommitted(
    context: ArtifactContext,
    target: Path,
    content_bytes: int,
) -> None:
    item = quarantine(target)
    if item is None:
        return
    relative = item.quarantine.relative_to(context.root).as_posix()
    try:
        context.catalog.record_retired(relative, content_bytes, _now_us())
    except BaseException:
        restore_quarantine(item)
        raise
    discard_quarantine(item)
    context.catalog.release_retired(relative)


def _now_us() -> int:
    return time.time_ns() // 1000


__all__ = ["commit_export", "commit_prepared_state"]
