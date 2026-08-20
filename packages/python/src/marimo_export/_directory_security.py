"""Rename-stable security metadata for directory transaction guards."""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

_UNSUPPORTED_ERRNOS = {
    errno.ENOSYS,
    getattr(errno, "ENOTSUP", errno.ENOSYS),
    getattr(errno, "EOPNOTSUPP", errno.ENOSYS),
}


@dataclass(frozen=True, slots=True)
class DirectorySecurityIdentity:
    flags: int | None
    xattrs: tuple[tuple[bytes, bytes], ...]
    acl_sha256: bytes | None
    windows_descriptor_sha256: bytes | None


def directory_security_identity(
    path: Path,
    metadata: os.stat_result,
) -> DirectorySecurityIdentity:
    return DirectorySecurityIdentity(
        flags=getattr(metadata, "st_flags", None),
        xattrs=_xattr_identity(path),
        acl_sha256=_digest(_posix_acl(path)),
        windows_descriptor_sha256=_digest(_windows_security_descriptor(path)),
    )


def _xattr_identity(path: Path) -> tuple[tuple[bytes, bytes], ...]:
    if sys.platform == "darwin":
        return _macos_xattr_identity(path)
    list_attributes = getattr(os, "listxattr", None)
    get_attribute = getattr(os, "getxattr", None)
    if list_attributes is None or get_attribute is None:
        return ()
    try:
        names = list_attributes(path, follow_symlinks=False)
    except OSError as error:
        if error.errno in _UNSUPPORTED_ERRNOS:
            return ()
        raise
    result: list[tuple[bytes, bytes]] = []
    for name in sorted(names, key=os.fsencode):
        value = get_attribute(path, name, follow_symlinks=False)
        result.append((os.fsencode(name), sha256(value).digest()))
    return tuple(result)


def _macos_xattr_identity(path: Path) -> tuple[tuple[bytes, bytes], ...]:
    library = ctypes.CDLL(None, use_errno=True)
    list_attributes = library.listxattr
    list_attributes.argtypes = (
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_size_t,
        ctypes.c_int,
    )
    list_attributes.restype = ctypes.c_ssize_t
    get_attribute = library.getxattr
    get_attribute.argtypes = (
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_uint32,
        ctypes.c_int,
    )
    get_attribute.restype = ctypes.c_ssize_t
    encoded_path = os.fsencode(path)
    nofollow = 0x0001
    ctypes.set_errno(0)
    size = list_attributes(encoded_path, None, 0, nofollow)
    if size < 0:
        error = ctypes.get_errno()
        if error in _UNSUPPORTED_ERRNOS:
            return ()
        raise OSError(error, os.strerror(error), path)
    if size == 0:
        return ()
    buffer = ctypes.create_string_buffer(size)
    actual = list_attributes(encoded_path, buffer, size, nofollow)
    if actual < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), path)
    names = sorted(name for name in bytes(buffer.raw[:actual]).split(b"\0") if name)
    result: list[tuple[bytes, bytes]] = []
    for name in names:
        value_size = get_attribute(encoded_path, name, None, 0, 0, nofollow)
        if value_size < 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), path)
        value = ctypes.create_string_buffer(value_size)
        value_actual = get_attribute(
            encoded_path,
            name,
            value,
            value_size,
            0,
            nofollow,
        )
        if value_actual < 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), path)
        result.append((name, sha256(value.raw[:value_actual]).digest()))
    return tuple(result)


def _posix_acl(path: Path) -> bytes | None:
    if sys.platform == "darwin":
        library = ctypes.CDLL(None, use_errno=True)
        acl_type = 0x00000100
    elif sys.platform.startswith("linux"):
        name = ctypes.util.find_library("acl")
        if name is None:
            return None
        try:
            library = ctypes.CDLL(name, use_errno=True)
        except OSError:
            return None
        acl_type = 0x00008000
    else:
        return None

    get_acl = library.acl_get_file
    get_acl.argtypes = (ctypes.c_char_p, ctypes.c_int)
    get_acl.restype = ctypes.c_void_p
    acl_size = library.acl_size
    acl_size.argtypes = (ctypes.c_void_p,)
    acl_size.restype = ctypes.c_ssize_t
    copy_acl = library.acl_copy_ext
    copy_acl.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t)
    copy_acl.restype = ctypes.c_ssize_t
    free_acl = library.acl_free
    free_acl.argtypes = (ctypes.c_void_p,)
    free_acl.restype = ctypes.c_int

    ctypes.set_errno(0)
    acl = get_acl(os.fsencode(path), acl_type)
    if not acl:
        error = ctypes.get_errno()
        if error == errno.ENOENT or error in _UNSUPPORTED_ERRNOS:
            return None
        raise OSError(error, os.strerror(error), path)
    try:
        size = acl_size(acl)
        if size < 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), path)
        buffer = ctypes.create_string_buffer(size)
        if copy_acl(buffer, acl, size) < 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), path)
        return bytes(buffer.raw)
    finally:
        free_acl(acl)


def _windows_security_descriptor(path: Path) -> bytes | None:
    if os.name != "nt":
        return None
    from ctypes import wintypes

    security_information = 0x00000001 | 0x00000002 | 0x00000004
    library = ctypes.WinDLL("advapi32", use_last_error=True)
    get_security = library.GetFileSecurityW
    get_security.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    get_security.restype = wintypes.BOOL
    required = wintypes.DWORD()
    ctypes.set_last_error(0)
    get_security(str(path), security_information, None, 0, ctypes.byref(required))
    error = ctypes.get_last_error()
    if error in {1, 50}:
        return None
    if error != 122:
        raise ctypes.WinError(error)
    buffer = ctypes.create_string_buffer(required.value)
    if not get_security(
        str(path),
        security_information,
        buffer,
        required.value,
        ctypes.byref(required),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return bytes(buffer.raw[: required.value])


def _digest(value: bytes | None) -> bytes | None:
    return None if value is None else sha256(value).digest()


__all__ = ["DirectorySecurityIdentity", "directory_security_identity"]
