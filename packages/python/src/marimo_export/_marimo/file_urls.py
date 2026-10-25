"""Parse the local file URLs that marimo writes into notebook outputs.

A virtual file URL such as ``./@file/29676-test.png`` names one buffer in the
kernel's virtual file registry by its byte length and filename. A public file
URL such as ``public/logo.png`` names a file below the notebook directory's
``public/`` folder. marimo serves both routes, and marimo-export closes them
into portable data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# Both patterns mirror marimo's own parsers. The marimo integration notes cite
# the source lines for the supported release. A byte length fits in 20 digits,
# which also keeps `int()` below the interpreter's digit limit.
_VIRTUAL_FILE_MEMBER = re.compile(r"(\d{1,20})-(.+)")
_PUBLIC_FILE = re.compile(r"(?:\./)?public/(.+)")


class VirtualFilePrefix(str, Enum):
    """One URL prefix that marimo resolves to its virtual file route."""

    RELATIVE = "./@file/"
    """The form ``VirtualFile.url`` writes for a registered file."""

    ROOT = "/@file/"
    """The key form of marimo's static export file table."""

    BARE = "@file/"
    """The form of marimo's empty virtual file, ``@file/0-empty.txt``."""


@dataclass(frozen=True, slots=True)
class VirtualFileUrl:
    """One marimo virtual file reference."""

    byte_length: int
    filename: str

    @classmethod
    def parse(cls, url: str, prefixes: tuple[VirtualFilePrefix, ...]) -> VirtualFileUrl | None:
        """Return the reference ``url`` names under one of ``prefixes``, or None."""

        prefix = next((prefix for prefix in prefixes if url.startswith(prefix)), None)
        if prefix is None:
            return None
        match = _VIRTUAL_FILE_MEMBER.fullmatch(url, len(prefix))
        if match is None:
            return None
        return cls(int(match.group(1)), match.group(2))

    @property
    def url(self) -> str:
        """The ``./@file/`` URL for this file, whichever prefix it was parsed from."""

        return f"{VirtualFilePrefix.RELATIVE.value}{self.byte_length}-{self.filename}"


@dataclass(frozen=True, slots=True)
class PublicFileUrl:
    """One reference to a file below the notebook's ``public/`` directory."""

    path: str

    @classmethod
    def parse(cls, url: str) -> PublicFileUrl | None:
        """Return the reference for ``public/<path>`` or ``./public/<path>``, or None."""

        match = _PUBLIC_FILE.fullmatch(url)
        return None if match is None else cls(match.group(1))

    def resolve(self, notebook_directory: Path) -> Path | None:
        """Return the regular file this reference names, or None.

        The path must be relative, and the file it resolves to after following
        symlinks must stay inside ``notebook_directory / "public"``. A ``..``
        segment that stays inside ``public/`` resolves like marimo's server
        route. Absolute, drive, and UNC paths, paths that resolve outside
        ``public/``, symlink loops, directories, and missing files return None.
        """

        # Reject anchored paths before `resolve()` touches them. On Windows,
        # `//host/share` would otherwise reach a network share.
        if Path(self.path).anchor:
            return None
        public_directory = notebook_directory / "public"
        try:
            candidate = (public_directory / self.path).resolve(strict=True)
            root = public_directory.resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            # Python 3.12 and earlier raise RuntimeError for a symlink loop.
            return None
        if not candidate.is_relative_to(root) or not candidate.is_file():
            return None
        return candidate


__all__ = ["PublicFileUrl", "VirtualFilePrefix", "VirtualFileUrl"]
