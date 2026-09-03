"""Native directory exchange for application delivery transactions."""

from __future__ import annotations

import ctypes
import errno
import os
import sys
from pathlib import Path

_RENAME_EXCHANGE = 0x00000002
_UNSUPPORTED_EXCHANGE_ERRORS = {
    errno.EINVAL,
    errno.ENOSYS,
    errno.EXDEV,
    getattr(errno, "ENOTSUP", errno.EINVAL),
    getattr(errno, "EOPNOTSUPP", errno.EINVAL),
}


def exchange_directories(first: Path, second: Path) -> bool:
    if not (sys.platform == "darwin" or sys.platform.startswith("linux")):
        return False
    library = ctypes.CDLL(None, use_errno=True)
    first_path = os.fsencode(first)
    second_path = os.fsencode(second)
    if sys.platform == "darwin":
        rename = getattr(library, "renamex_np", None)
        arguments = (first_path, second_path, _RENAME_EXCHANGE)
        argument_types = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    elif sys.platform.startswith("linux"):
        rename = getattr(library, "renameat2", None)
        arguments = (-100, first_path, -100, second_path, _RENAME_EXCHANGE)
        argument_types = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
    if rename is None:
        return False
    rename.argtypes = argument_types
    rename.restype = ctypes.c_int
    if rename(*arguments) == 0:
        return True
    error = ctypes.get_errno()
    if error in _UNSUPPORTED_EXCHANGE_ERRORS:
        return False
    raise OSError(error, os.strerror(error), second)


__all__ = ["exchange_directories"]
