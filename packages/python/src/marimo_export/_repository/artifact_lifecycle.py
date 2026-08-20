from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path, PurePosixPath
from uuid import uuid4

from marimo_export._repository.artifact_access import (
    generation_state_fingerprints,
    state_key_for,
)
from marimo_export._repository.artifact_context import ArtifactContext
from marimo_export._repository.files import (
    QuarantinedArtifact,
    discard_quarantine,
    discard_staging,
    physical_tree_bytes,
    quarantine,
    remove_tree,
    restore_quarantine,
    verify_prepared_state,
    walk_tree,
)
from marimo_export._repository.models import PruneResult, RepositoryIntegrityError
from marimo_export._repository.paths import (
    export_path,
    private_directory,
    staging_root,
    sync_directory,
)
from marimo_export._repository.sqlite.records import GenerationRow, StateRow
from marimo_export._repository.sqlite.retention import RetentionVictims
from marimo_export.errors import NotebookExportError

_RECOVERY = re.compile(r"\.([0-9a-f]{64})-(?:incomplete-[0-9]+|quarantine-[0-9a-f]{32})")
_UNINDEXED = re.compile(r"\.(?:prepared-states|exports|staging)-unindexed-[0-9a-f]{32}")
_CATALOG_SNAPSHOT = re.compile(
    r"\.catalog\.sqlite3(?:-wal|-shm)?\.(?:corrupt|incompatible)-[0-9a-f]{32}"
)


def new_staging(context: ArtifactContext) -> Path:
    root = private_directory(staging_root(context.root))
    path = private_directory(root / f"stage-{uuid4().hex}")
    relative = path.relative_to(context.root).as_posix()
    try:
        context.leases.reserve_staging(relative, path)
    except BaseException:
        remove_tree(path)
        raise
    return path


def discard_owned_staging(context: ArtifactContext, path: Path) -> None:
    relative = discard_staging(path, context.root)
    context.leases.release_staging(relative)


def retire_unindexed_artifacts(context: ArtifactContext) -> None:
    """Account and retire artifact roots after an incompatible catalog reset."""

    for name in ("prepared-states", "exports", "staging"):
        source = context.root / name
        if not source.exists() and not source.is_symlink():
            continue
        retired = context.root / f".{name}-unindexed-{uuid4().hex}"
        content_bytes = physical_tree_bytes(
            source,
            limit=context.limits.repository_bytes,
        )
        os.replace(source, retired)
        sync_directory(context.root)
        relative = retired.relative_to(context.root).as_posix()
        try:
            context.catalog.record_retired(relative, content_bytes, _now_us())
        except BaseException:
            os.replace(retired, source)
            sync_directory(context.root)
            raise
    _cleanup_retired(context)


def retire_catalog_snapshots(
    context: ArtifactContext,
    snapshots: tuple[Path, ...],
) -> None:
    for snapshot in snapshots:
        if snapshot.parent != context.root or _CATALOG_SNAPSHOT.fullmatch(snapshot.name) is None:
            raise OSError("Repository catalog snapshot path is invalid")
        if not snapshot.exists() and not snapshot.is_symlink():
            continue
        context.catalog.record_retired(
            snapshot.name,
            snapshot.lstat().st_size,
            _now_us(),
        )
    _cleanup_retired(context)


def admit(context: ArtifactContext, additional_bytes: int) -> None:
    del additional_bytes
    prune(context, dry_run=False)


def prune(context: ArtifactContext, *, dry_run: bool) -> PruneResult:
    context.leases.flush_releases()
    candidates = context.catalog.prune_snapshot(
        limits=context.limits,
        now_us=_now_us(),
        dry_run=dry_run,
    )
    if dry_run:
        return PruneResult(
            prepared_states=len(candidates.states),
            generations=len(candidates.generations),
            bytes_released=candidates.content_bytes,
            dry_run=True,
        )
    quarantined: list[QuarantinedArtifact] = []
    retired: dict[Path, tuple[str, int]] = {}
    quarantine_keys: dict[Path, tuple[str, str, str]] = {}

    def quarantine_victims(victims: RetentionVictims) -> None:
        try:
            for row in victims.generations:
                item = quarantine(export_path(context.root, row.identity, row.instance))
                if item is not None:
                    quarantined.append(item)
                    retired[item.quarantine] = (
                        item.quarantine.relative_to(context.root).as_posix(),
                        row.content_bytes,
                    )
                    quarantine_keys[item.quarantine] = (
                        "generation",
                        row.identity_key,
                        row.instance,
                    )
            for row in victims.states:
                item = quarantine(context.state_path(row))
                if item is not None:
                    quarantined.append(item)
                    retired[item.quarantine] = (
                        item.quarantine.relative_to(context.root).as_posix(),
                        row.content_bytes,
                    )
                    quarantine_keys[item.quarantine] = (
                        "state",
                        row.state_key,
                        row.instance,
                    )
        except BaseException:
            _restore_all(quarantined)
            raise

    quarantine_victims(candidates)
    retired_states = {
        (key, instance): retired[path]
        for path, (kind, key, instance) in quarantine_keys.items()
        if kind == "state"
    }
    retired_generations = {
        (key, instance): retired[path]
        for path, (kind, key, instance) in quarantine_keys.items()
        if kind == "generation"
    }

    try:
        victims = context.catalog.commit_prune(
            candidates=candidates,
            retired_states=retired_states,
            retired_generations=retired_generations,
            limits=context.limits,
            now_us=_now_us(),
        )
    except BaseException:
        _restore_all(quarantined)
        raise
    matched = {
        *(("state", row.state_key, row.instance) for row in victims.states),
        *(("generation", row.identity_key, row.instance) for row in victims.generations),
    }
    for item in quarantined:
        if quarantine_keys[item.quarantine] not in matched:
            restore_quarantine(item)
            continue
        relative, _content_bytes = retired[item.quarantine]
        discard_quarantine(item)
        context.catalog.release_retired(relative)
    return PruneResult(
        prepared_states=len(victims.states),
        generations=len(victims.generations),
        bytes_released=victims.content_bytes,
        dry_run=dry_run,
    )


def recover(context: ArtifactContext) -> None:
    quarantined: list[QuarantinedArtifact] = []
    retired: dict[Path, tuple[str, int]] = {}
    quarantine_keys: dict[Path, tuple[str, str, str]] = {}
    _cleanup_retired(context)
    staging_candidates = _staging_candidates(context)
    snapshot = context.catalog.recovery_snapshot(_now_us())

    def state_valid(row: StateRow) -> bool:
        try:
            manifest = verify_prepared_state(context.state_path(row))
            return (
                manifest.instance == row.instance
                and manifest.producer_sha256 == row.producer_sha256
                and manifest.output_plan_sha256 == row.output_plan_sha256
                and manifest.state_fingerprint == row.state_fingerprint
                and state_key_for(
                    manifest.producer_sha256,
                    manifest.output_plan_sha256,
                    manifest.state_fingerprint,
                )
                == row.state_key
                and manifest.closure.content_bytes == row.content_bytes
                and _canonical_metadata(manifest.metadata) == row.metadata
                and row.metadata_bytes == len(row.metadata)
            )
        except (NotebookExportError, RepositoryIntegrityError):
            return False

    def generation_valid(row: GenerationRow) -> bool:
        try:
            instance, closure = context.verify_export(
                export_path(context.root, row.identity, row.instance)
            )
            generation_state_fingerprints(row.metadata, row.instance)
            return (
                instance == row.instance
                and row.metadata_bytes == len(row.metadata)
                and closure.content_bytes == row.content_bytes
            )
        except (NotebookExportError, RepositoryIntegrityError):
            return False

    def quarantine_invalid(
        states: tuple[StateRow, ...],
        generations: tuple[GenerationRow, ...],
    ) -> None:
        try:
            for row in generations:
                item = quarantine(export_path(context.root, row.identity, row.instance))
                if item is not None:
                    quarantined.append(item)
                    retired[item.quarantine] = (
                        item.quarantine.relative_to(context.root).as_posix(),
                        row.content_bytes,
                    )
                    quarantine_keys[item.quarantine] = (
                        "generation",
                        row.identity_key,
                        row.instance,
                    )
            for row in states:
                item = quarantine(context.state_path(row))
                if item is not None:
                    quarantined.append(item)
                    retired[item.quarantine] = (
                        item.quarantine.relative_to(context.root).as_posix(),
                        row.content_bytes,
                    )
                    quarantine_keys[item.quarantine] = (
                        "state",
                        row.state_key,
                        row.instance,
                    )
        except BaseException:
            _restore_all(quarantined)
            raise

    _remove_abandoned_staging(
        context,
        snapshot.active_staging,
        staging_candidates,
    )
    _recover_backups(context, snapshot.states, snapshot.generations)
    _reconcile_orphan_artifacts(context, snapshot.states, snapshot.generations)
    invalid_states = tuple(
        row
        for row in snapshot.states
        if row.producer_sha256 in snapshot.corrupt_producers or not state_valid(row)
    )
    invalid_generations = tuple(
        row
        for row in snapshot.generations
        if row.identity.producer_sha256 in snapshot.corrupt_producers or not generation_valid(row)
    )
    quarantine_invalid(invalid_states, invalid_generations)
    retired_states = {
        (key, instance): retired[path]
        for path, (kind, key, instance) in quarantine_keys.items()
        if kind == "state"
    }
    retired_generations = {
        (key, instance): retired[path]
        for path, (kind, key, instance) in quarantine_keys.items()
        if kind == "generation"
    }

    try:
        matched_states, matched_generations = context.catalog.recover_artifacts(
            snapshot=snapshot,
            now_us=_now_us(),
            invalid_states=invalid_states,
            invalid_generations=invalid_generations,
            retired_states=retired_states,
            retired_generations=retired_generations,
        )
    except BaseException:
        _restore_all(quarantined)
        raise
    matched = {
        *(("state", row.state_key, row.instance) for row in matched_states),
        *(("generation", row.identity_key, row.instance) for row in matched_generations),
    }
    for item in quarantined:
        if quarantine_keys[item.quarantine] not in matched:
            restore_quarantine(item)
            continue
        relative, _content_bytes = retired[item.quarantine]
        discard_quarantine(item)
        context.catalog.release_retired(relative)


def _staging_candidates(context: ArtifactContext) -> tuple[Path, ...]:
    root = staging_root(context.root)
    if not root.exists() or root.is_symlink() or not root.is_dir():
        return ()
    return tuple(child for child in root.iterdir() if child.name.startswith("stage-"))


def _remove_abandoned_staging(
    context: ArtifactContext,
    active: frozenset[str],
    candidates: tuple[Path, ...],
) -> None:
    for child in candidates:
        relative = child.relative_to(context.root).as_posix()
        if relative not in active or child.is_symlink() or not child.is_dir():
            remove_tree(child)


def _remove_orphans(root: Path, known: set[Path]) -> None:
    if not root.exists() or root.is_symlink() or not root.is_dir():
        return
    for current, directories, _files in walk_tree(root, topdown=False):
        current_path = Path(current)
        for name in tuple(directories):
            child = current_path / name
            if any(path == child or path.is_relative_to(child) for path in known):
                continue
            if child.is_symlink():
                child.unlink(missing_ok=True)
                continue
            if _digest_name(child.name):
                remove_tree(child)
            else:
                with suppress(OSError):
                    child.rmdir()


def _recover_backups(
    context: ArtifactContext,
    states: tuple[StateRow, ...],
    generations: tuple[GenerationRow, ...],
) -> None:
    for row in states:
        target = context.state_path(row)

        def valid_state(path: Path, row: StateRow = row) -> bool:
            try:
                manifest = verify_prepared_state(path)
                return (
                    manifest.instance == row.instance
                    and manifest.producer_sha256 == row.producer_sha256
                    and manifest.output_plan_sha256 == row.output_plan_sha256
                    and manifest.state_fingerprint == row.state_fingerprint
                    and state_key_for(
                        manifest.producer_sha256,
                        manifest.output_plan_sha256,
                        manifest.state_fingerprint,
                    )
                    == row.state_key
                    and manifest.closure.content_bytes == row.content_bytes
                    and _canonical_metadata(manifest.metadata) == row.metadata
                    and row.metadata_bytes == len(row.metadata)
                )
            except (NotebookExportError, RepositoryIntegrityError):
                return False

        _recover_one(target, row.instance, valid_state)
    for row in generations:
        target = export_path(context.root, row.identity, row.instance)

        def valid_generation(path: Path, row: GenerationRow = row) -> bool:
            try:
                instance, closure = context.verify_export(path)
                return instance == row.instance and closure.content_bytes == row.content_bytes
            except (NotebookExportError, RepositoryIntegrityError):
                return False

        _recover_one(target, row.instance, valid_generation)


def _reconcile_orphan_artifacts(
    context: ArtifactContext,
    states: tuple[StateRow, ...],
    generations: tuple[GenerationRow, ...],
) -> None:
    known = {
        *(context.state_path(row) for row in states),
        *(export_path(context.root, row.identity, row.instance) for row in generations),
    }
    candidates: list[Path] = []
    state_root = context.root / "prepared-states"
    if state_root.is_dir() and not state_root.is_symlink():
        candidates.extend(
            path
            for path in state_root.glob("*/*/*/*")
            if path not in known and all(_digest_name(part) for part in path.parts[-4:])
        )
    export_root = context.root / "exports"
    if export_root.is_dir() and not export_root.is_symlink():
        candidates.extend(
            path
            for path in export_root.glob("*/*")
            if path not in known and all(_digest_name(part) for part in path.parts[-2:])
        )
    for candidate in candidates:
        content_bytes = physical_tree_bytes(
            candidate,
            limit=context.limits.repository_bytes,
        )
        item = quarantine(candidate)
        if item is None:
            continue
        relative = item.quarantine.relative_to(context.root).as_posix()
        try:
            context.catalog.record_retired(relative, content_bytes, _now_us())
        except BaseException:
            restore_quarantine(item)
            raise
        discard_quarantine(item)
        context.catalog.release_retired(relative)


def _recover_one(
    target: Path,
    instance: str,
    valid: Callable[[Path], bool],
) -> None:
    parent = target.parent
    if not parent.is_dir() or parent.is_symlink():
        return
    candidates = sorted(
        (
            child
            for child in parent.iterdir()
            if (match := _RECOVERY.fullmatch(child.name)) is not None and match.group(1) == instance
        ),
        reverse=True,
    )
    if valid(target):
        for candidate in candidates:
            remove_tree(candidate)
        if candidates:
            sync_directory(parent)
        return
    replacement = next((candidate for candidate in candidates if valid(candidate)), None)
    if replacement is not None:
        if target.exists() or target.is_symlink():
            remove_tree(target)
        os.replace(replacement, target)
        sync_directory(parent)
    for candidate in candidates:
        if candidate.exists() or candidate.is_symlink():
            remove_tree(candidate)
    if candidates:
        sync_directory(parent)


def _remove_recovery_candidates(root: Path) -> None:
    if not root.is_dir() or root.is_symlink():
        return
    changed: set[Path] = set()
    for current, directories, _files in walk_tree(root):
        parent = Path(current)
        for name in tuple(directories):
            child = parent / name
            if _RECOVERY.fullmatch(name) is None:
                continue
            directories.remove(name)
            remove_tree(child)
            changed.add(parent)
    for parent in changed:
        sync_directory(parent)


def _cleanup_retired(context: ArtifactContext) -> None:
    for relative, content_bytes in context.catalog.retired_artifacts():
        normalized = PurePosixPath(relative)
        if (
            normalized.is_absolute()
            or ".." in normalized.parts
            or normalized.as_posix() != relative
            or (
                _RECOVERY.fullmatch(normalized.name) is None
                and _UNINDEXED.fullmatch(normalized.name) is None
                and _CATALOG_SNAPSHOT.fullmatch(normalized.name) is None
            )
            or (
                _UNINDEXED.fullmatch(normalized.name) is None
                and _CATALOG_SNAPSHOT.fullmatch(normalized.name) is None
                and normalized.parts[0] not in {"prepared-states", "exports"}
            )
        ):
            context.catalog.release_retired(relative)
            continue
        path = context.root.joinpath(*normalized.parts)
        if content_bytes is None:
            content_bytes = physical_tree_bytes(
                path,
                limit=context.limits.repository_bytes,
            )
            context.catalog.record_retired(relative, content_bytes, _now_us())
        item = QuarantinedArtifact(path, path)
        discard_quarantine(item)
        context.catalog.release_retired(relative)


def _canonical_metadata(value: object) -> bytes:
    from marimo_export._json import canonical_bytes

    return canonical_bytes(value)


def _restore_all(items: list[QuarantinedArtifact]) -> None:
    for item in reversed(items):
        restore_quarantine(item)


def _now_us() -> int:
    return time.time_ns() // 1000


def _digest_name(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


__all__ = [
    "admit",
    "discard_owned_staging",
    "new_staging",
    "prune",
    "recover",
    "retire_catalog_snapshots",
    "retire_unindexed_artifacts",
]
