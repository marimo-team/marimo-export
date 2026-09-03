"""Cross-platform failure-atomic directory transactions."""

from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path

from marimo_export._diagnostics import record_cleanup_failure
from marimo_export._directory_native import exchange_directories as _exchange_directories
from marimo_export._directory_target import (
    DirectoryIdentity,
    DirectoryTarget,
    DirectoryTransactionError,
    directory_identity,
    new_staging_directory,
    preflight_directory,
)


def commit_directory(
    staged: Path,
    target: DirectoryTarget,
    *,
    retain_replaced: bool = False,
) -> Path | None:
    if not isinstance(retain_replaced, bool):
        raise TypeError("retain_replaced must be a boolean")
    if target.identity is None:
        _commit_absent(staged, target.path)
        return None
    try:
        exchanged, replaced = _commit_with_exchange(
            staged,
            target,
            retain_replaced=retain_replaced,
        )
        if exchanged:
            return replaced
        return _commit_with_rollback(
            staged,
            target,
            retain_replaced=retain_replaced,
        )
    except DirectoryTransactionError:
        raise
    except OSError as error:
        raise DirectoryTransactionError(
            "export_commit_failed",
            f"could not replace directory {target.path}: {error}",
        ) from error


def _commit_absent(staged: Path, output: Path) -> None:
    try:
        output.mkdir()
    except FileExistsError as error:
        raise DirectoryTransactionError(
            "destination_changed",
            f"destination changed while delivery was staged: {output}",
        ) from error
    if os.name == "nt":
        output.rmdir()
    try:
        os.replace(staged, output)
    except OSError as error:
        if os.name != "nt":
            with suppress(OSError):
                output.rmdir()
        raise DirectoryTransactionError(
            "export_commit_failed",
            f"could not create directory {output}: {error}",
        ) from error


def _restore_previous(
    previous: Path,
    output: Path,
    *,
    discard_output: bool,
    primary: BaseException | None = None,
) -> None:
    if not previous.exists() and not previous.is_symlink():
        return
    displaced: Path | None = None
    if output.exists() or output.is_symlink():
        displaced = _empty_sibling(output, "interrupted")
        os.replace(output, displaced)
    try:
        os.replace(previous, output)
    except BaseException:
        if displaced is not None:
            os.replace(displaced, output)
        raise
    if displaced is None:
        return
    if discard_output:
        try:
            _remove(displaced)
        except BaseException as cleanup_error:
            if primary is None:
                raise
            record_cleanup_failure(
                primary,
                "interrupted directory cleanup",
                cleanup_error,
            )
        return
    raise DirectoryTransactionError(
        "export_commit_failed",
        f"previous output is preserved at {output}. Interrupted output is preserved at {displaced}",
    )


def _commit_with_exchange(
    staged: Path,
    target: DirectoryTarget,
    *,
    retain_replaced: bool,
) -> tuple[bool, Path | None]:
    output = target.path
    candidate_identity = directory_identity(staged)
    if not _exchange_directories(staged, output):
        return False, None
    try:
        if directory_identity(staged) != target.identity:
            _fail(
                "destination_changed",
                f"destination changed while delivery was staged: {output}",
            )
    except BaseException as error:
        try:
            restored = _exchange_directories(staged, output)
        except BaseException as restore_error:
            recovery = _preserve(staged, output, "recovery")
            raise DirectoryTransactionError(
                "export_commit_failed",
                f"could not restore previous output at {output}: {restore_error}. "
                f"Previous output is preserved at {recovery}",
            ) from error
        if not restored:
            _restore_previous(staged, output, discard_output=False)
        try:
            displaced = directory_identity(staged) != candidate_identity
        except OSError:
            displaced = False
        if displaced:
            interrupted = _preserve(staged, output, "interrupted")
            raise DirectoryTransactionError(
                "export_commit_failed",
                f"previous output is preserved at {output}. Interrupted output is "
                f"preserved at {interrupted}",
            ) from error
        raise
    if retain_replaced:
        return True, staged
    _remove(staged)
    return True, None


def _commit_with_rollback(
    staged: Path,
    target: DirectoryTarget,
    *,
    retain_replaced: bool,
) -> Path | None:
    output = target.path
    previous = _empty_sibling(output, "recovery")
    moved_previous = False
    try:
        os.replace(output, previous)
        moved_previous = True
        if directory_identity(previous) != target.identity:
            _fail(
                "destination_changed",
                f"destination changed while delivery was staged: {output}",
            )
        _commit_absent(staged, output)
    except BaseException as error:
        if moved_previous:
            try:
                _restore_previous(
                    previous,
                    output,
                    discard_output=not staged.exists(),
                    primary=error,
                )
            except BaseException as restore_error:
                if not previous.exists():
                    raise
                raise DirectoryTransactionError(
                    "export_commit_failed",
                    f"could not restore previous output at {output}: {restore_error}. "
                    f"Previous output is preserved at {previous}",
                ) from error
        raise
    if retain_replaced:
        return previous
    _remove(previous)
    return None


def _empty_sibling(output: Path, label: str) -> Path:
    candidate = Path(
        tempfile.mkdtemp(
            dir=output.parent,
            prefix=f".{output.name}.{label}-",
        )
    )
    candidate.rmdir()
    return candidate


def _preserve(staged: Path, output: Path, label: str) -> Path:
    candidate = _empty_sibling(output, label)
    os.replace(staged, candidate)
    return candidate


def _remove(path: Path) -> None:
    shutil.rmtree(path)


def sync_directory(path: Path) -> None:
    if os.name != "posix" or not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fail(code: str, message: str) -> None:
    raise DirectoryTransactionError(code, message)


__all__ = [
    "DirectoryIdentity",
    "DirectoryTarget",
    "DirectoryTransactionError",
    "commit_directory",
    "directory_identity",
    "new_staging_directory",
    "preflight_directory",
    "sync_directory",
]
