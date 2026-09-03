from __future__ import annotations

import errno
import io
import os
import stat
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

_READ_BUFFER_BYTES = 64 * 1024


class SecureReadError(OSError):
    """Base error for a verified export file read."""


class SecureReadUnavailableError(SecureReadError):
    """A secure export read failed because its storage is unavailable."""


class SecureReadLimitError(SecureReadError):
    """A regular file exceeds the configured read limit."""

    def __init__(self, *, actual_size: int, limit: int) -> None:
        super().__init__(f"file size {actual_size} exceeds the {limit} byte read limit")
        self.actual_size = actual_size
        self.limit = limit


class SecureFileSizeError(SecureReadError):
    """A regular file differs from its required or inspected size."""

    def __init__(self, *, expected_size: int, actual_size: int) -> None:
        super().__init__(f"file size changed from {expected_size} bytes to {actual_size} bytes")
        self.expected_size = expected_size
        self.actual_size = actual_size


def read_export_index(root: Path, *, max_bytes: int) -> bytes:
    """Read ``index.json`` through verified platform file handles."""

    _validate_max_bytes(max_bytes)
    return _translate_read_errors(
        lambda: _read_relative_file(
            root,
            ("index.json",),
            max_bytes=max_bytes,
            expected_size=None,
        )
    )


def read_export_asset(
    root: Path,
    relative_path: str,
    *,
    expected_size: int,
    max_bytes: int,
) -> bytes:
    """Read one cache asset and require its indexed byte size."""

    _validate_max_bytes(max_bytes)
    _validate_expected_size(expected_size)
    if expected_size > max_bytes:
        raise SecureReadLimitError(actual_size=expected_size, limit=max_bytes)
    if not isinstance(relative_path, str):
        raise TypeError("relative_path must be a string")
    components = tuple(relative_path.split("/"))
    if len(components) != 2 or components[0] != "assets":
        raise SecureReadError("export asset path is invalid")
    try:
        _validate_component(components[1])
    except ValueError as error:
        raise SecureReadError("export asset path is invalid") from error
    return _translate_read_errors(
        lambda: _read_relative_file(
            root,
            components,
            max_bytes=max_bytes,
            expected_size=expected_size,
        )
    )


def _read_relative_file(
    root: Path,
    components: tuple[str, ...],
    *,
    max_bytes: int,
    expected_size: int | None,
) -> bytes:
    if _descriptor_relative_reads_supported():
        return _read_relative_file_at(
            root,
            components,
            max_bytes=max_bytes,
            expected_size=expected_size,
        )
    if _windows_fallback_supported():
        return _read_relative_file_on_windows(
            root,
            components,
            max_bytes=max_bytes,
            expected_size=expected_size,
        )
    raise SecureReadError("secure export file reads are unavailable on this platform")


def _read_relative_file_at(
    root: Path,
    components: tuple[str, ...],
    *,
    max_bytes: int,
    expected_size: int | None,
) -> bytes:
    root_components = _root_components(root)
    parent_fd = _open_root(root_components)
    try:
        for component in components[:-1]:
            child_fd = _open_directory_at(parent_fd, component)
            _close_fd(parent_fd)
            parent_fd = child_fd
        file_fd = _open_file_at(parent_fd, components[-1])
        try:
            return _read_regular_file(
                file_fd,
                max_bytes=max_bytes,
                expected_size=expected_size,
            )
        finally:
            _close_fd(file_fd)
    finally:
        _close_fd(parent_fd)


def _read_relative_file_on_windows(
    root: Path,
    components: tuple[str, ...],
    *,
    max_bytes: int,
    expected_size: int | None,
) -> bytes:
    """Read a file after repeated reparse, containment, and identity checks.

    The fallback requires a stable export tree until the second
    inspection completes because standard Python Windows file opens are path
    based.
    """

    path, inspected = _inspect_windows_path(root, components)
    file_fd = os.open(path, _windows_file_flags())
    try:
        opened = os.fstat(file_fd)
        _require_same_file(inspected, opened)
        _, reinspected = _inspect_windows_path(root, components)
        _require_same_file(reinspected, opened)
        return _read_regular_file(
            file_fd,
            max_bytes=max_bytes,
            expected_size=expected_size,
        )
    finally:
        _close_fd(file_fd)


def _inspect_windows_path(
    root: Path,
    components: tuple[str, ...],
) -> tuple[Path, os.stat_result]:
    _validate_absolute_root(root)
    current = Path(root.anchor)
    for component in root.parts[1:]:
        _validate_root_component(component)
        current /= component
        inspected = os.lstat(current)
        _reject_reparse_point(inspected)
        if not stat.S_ISDIR(inspected.st_mode):
            raise SecureReadError("export path component is not a directory")
    for component in components[:-1]:
        _validate_component(component)
        current /= component
        inspected = os.lstat(current)
        _reject_reparse_point(inspected)
        if not stat.S_ISDIR(inspected.st_mode):
            raise SecureReadError("export path component is not a directory")

    leaf = current / components[-1]
    inspected = os.lstat(leaf)
    _reject_reparse_point(inspected)
    if not stat.S_ISREG(inspected.st_mode):
        raise SecureReadError("opened path is not a regular file")
    _require_inside(root, leaf)
    return leaf, inspected


def _reject_reparse_point(inspected: object) -> None:
    mode = getattr(inspected, "st_mode", 0)
    attributes = getattr(inspected, "st_file_attributes", 0)
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if stat.S_ISLNK(mode) or attributes & reparse_attribute:
        raise SecureReadError("export paths cannot contain reparse points")


def _require_inside(root: Path, path: Path) -> None:
    try:
        resolved_root = root.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
        resolved_path.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError) as error:
        raise SecureReadError("opened path resolves outside the export") from error


def _require_same_file(inspected: os.stat_result, opened: os.stat_result) -> None:
    if inspected.st_ino == 0 or opened.st_ino == 0 or not os.path.samestat(inspected, opened):
        raise SecureReadError("opened file identity changed during validation")


def _open_root(components: tuple[str, ...]) -> int:
    current_fd = os.open(os.sep, _directory_flags())
    try:
        for component in components:
            _validate_root_component(component)
            child_fd = os.open(component, _directory_flags(), dir_fd=current_fd)
            _close_fd(current_fd)
            current_fd = child_fd
    except BaseException:
        _close_fd(current_fd)
        raise
    return current_fd


def _open_directory_at(parent_fd: int, component: str) -> int:
    _validate_component(component)
    return os.open(component, _directory_flags(), dir_fd=parent_fd)


def _open_file_at(parent_fd: int, component: str) -> int:
    _validate_component(component)
    return os.open(component, _file_flags(), dir_fd=parent_fd)


def _read_regular_file(
    file_fd: int,
    *,
    max_bytes: int,
    expected_size: int | None,
) -> bytes:
    inspected = os.fstat(file_fd)
    if not stat.S_ISREG(inspected.st_mode):
        raise SecureReadError("opened path is not a regular file")
    inspected_size = inspected.st_size
    if inspected_size < 0:
        raise SecureReadError("opened file reported a negative size")
    if inspected_size > max_bytes:
        raise SecureReadLimitError(actual_size=inspected_size, limit=max_bytes)
    if expected_size is not None and inspected_size != expected_size:
        raise SecureFileSizeError(
            expected_size=expected_size,
            actual_size=inspected_size,
        )

    data = _read_file_bytes(file_fd, inspected_size + 1)
    actual_size = len(data)
    if actual_size != inspected_size:
        raise SecureFileSizeError(
            expected_size=inspected_size,
            actual_size=actual_size,
        )
    final_size = os.fstat(file_fd).st_size
    if final_size != inspected_size:
        raise SecureFileSizeError(
            expected_size=inspected_size,
            actual_size=final_size,
        )
    return data


def _read_file_bytes(file_fd: int, maximum_bytes: int) -> bytes:
    buffer_size = min(_READ_BUFFER_BYTES, max(1, maximum_bytes))
    with (
        io.FileIO(file_fd, "rb", closefd=False) as raw,
        io.BufferedReader(raw, buffer_size=buffer_size) as stream,
    ):
        return stream.read(maximum_bytes)


def _root_components(root: Path) -> tuple[str, ...]:
    _validate_absolute_root(root)
    if root.anchor != os.sep:
        raise ValueError("root must be an absolute POSIX path")
    components = root.parts[1:]
    return components


def _validate_absolute_root(root: Path) -> None:
    if not isinstance(root, Path):
        raise TypeError("root must be a pathlib.Path")
    if not root.is_absolute():
        raise ValueError("root must be an absolute path")
    for component in root.parts[1:]:
        _validate_root_component(component)


def _validate_root_component(component: str) -> None:
    if not component or component in {".", ".."} or "/" in component or "\x00" in component:
        raise ValueError("root path components must be valid names")


def _validate_component(component: str) -> None:
    if (
        not component
        or component in {".", ".."}
        or "/" in component
        or "\\" in component
        or "\x00" in component
    ):
        raise ValueError("path components must be non-empty portable names")


def _validate_max_bytes(max_bytes: object) -> None:
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise TypeError("max_bytes must be a positive integer")


def _validate_expected_size(expected_size: object) -> None:
    if not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size < 0:
        raise TypeError("expected_size must be a non-negative integer")


def _descriptor_relative_reads_supported() -> bool:
    return (
        os.name == "posix"
        and os.open in os.supports_dir_fd
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
    )


def _windows_fallback_supported() -> bool:
    return os.name == "nt" and hasattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT")


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _file_flags() -> int:
    return os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)


def _windows_file_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )


def _close_fd(file_fd: int) -> None:
    with suppress(OSError):
        os.close(file_fd)


def _translate_read_errors(read: Callable[[], bytes]) -> bytes:
    try:
        return read()
    except SecureReadError:
        raise
    except ValueError as error:
        raise SecureReadError(str(error)) from error
    except (PermissionError, TimeoutError, BlockingIOError) as error:
        raise SecureReadUnavailableError("file storage is unavailable") from error
    except (OverflowError, MemoryError) as error:
        raise SecureReadUnavailableError("file resources are unavailable") from error
    except OSError as error:
        if error.errno in {errno.ENOENT, errno.ENOTDIR, errno.EISDIR, errno.ELOOP}:
            raise SecureReadError("export file structure is invalid") from error
        raise SecureReadUnavailableError("file storage is unavailable") from error


__all__ = [
    "SecureFileSizeError",
    "SecureReadError",
    "SecureReadLimitError",
    "SecureReadUnavailableError",
    "read_export_asset",
    "read_export_index",
]
