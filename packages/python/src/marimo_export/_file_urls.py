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
# the source lines for the supported release.
_VIRTUAL_FILE_MEMBER = re.compile(r"(\d+)-(.+)")
_PUBLIC_FILE = re.compile(r"(?:\./)?public/(.+)")


class VirtualFilePrefix(str, Enum):
    """One URL prefix that marimo resolves to its virtual file route."""

    RELATIVE = "./@file/"
    """The form ``VirtualFile.url`` writes into outputs."""

    ROOT = "/@file/"
    """The key form of marimo's static export file table."""

    BARE = "@file/"
    """The form of marimo's empty virtual file."""


@dataclass(frozen=True, slots=True)
class VirtualFileUrl:
    """One marimo virtual file reference."""

    byte_length: int
    filename: str

    @classmethod
    def parse(cls, url: str, prefixes: tuple[VirtualFilePrefix, ...]) -> VirtualFileUrl | None:
        """Return the reference ``url`` names under one of ``prefixes``, or None."""

        prefix = next((prefix for prefix in prefixes if url.startswith(prefix.value)), None)
        if prefix is None:
            return None
        match = _VIRTUAL_FILE_MEMBER.fullmatch(url, len(prefix.value))
        if match is None:
            return None
        try:
            byte_length = int(match.group(1))
        except ValueError:
            # `int()` rejects digit strings beyond the interpreter's conversion limit.
            return None
        return cls(byte_length, match.group(2))

    @property
    def url(self) -> str:
        """The relative URL that marimo writes for this file."""

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

        The resolved file must stay inside ``notebook_directory / "public"``
        after following symlinks. Traversal segments, absolute paths, symlink
        escapes, symlink loops, directories, and missing files return None.
        """

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
