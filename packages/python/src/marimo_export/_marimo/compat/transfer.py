"""Private marimo virtual-file adapter."""

from __future__ import annotations


class PrivateTransferRuntime:
    """Create temporary files owned by the attached marimo runtime."""

    def context(self) -> object:
        from marimo._runtime.context import get_context

        return get_context()

    def create_virtual_file(self, data: bytes) -> object:
        from marimo._runtime.virtual_file import VirtualFile, random_filename

        return VirtualFile(filename=random_filename("bin"), buffer=data)


__all__ = ["PrivateTransferRuntime"]
