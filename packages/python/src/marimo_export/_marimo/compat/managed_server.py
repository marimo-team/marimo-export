from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import msgspec

_MANAGED_SOURCE_ENV = "MARIMO_EXPORT_MANAGED_SOURCE"
_MANAGED_SNAPSHOT_ENV = "MARIMO_EXPORT_MANAGED_SNAPSHOT"


def _install_runtime_filename() -> None:
    source = os.environ.pop(_MANAGED_SOURCE_ENV, None)
    snapshot = os.environ.pop(_MANAGED_SNAPSHOT_ENV, None)
    if source is None or snapshot is None:
        raise RuntimeError("managed notebook paths are unavailable")
    source_path = str(Path(source).resolve(strict=True))
    snapshot_path = str(Path(snapshot).resolve(strict=True))

    from marimo._internal.commands import AppMetadata
    from marimo._session.session import SessionImpl

    native = SessionImpl.create

    async def create(cls: type, **kwargs: Any) -> Any:
        del cls
        metadata = kwargs.get("app_metadata")
        if (
            isinstance(metadata, AppMetadata)
            and str(Path(metadata.filename).resolve(strict=True)) == snapshot_path
        ):
            kwargs["app_metadata"] = msgspec.structs.replace(
                metadata,
                filename=source_path,
            )
        return await native(**kwargs)

    cast(Any, SessionImpl).create = classmethod(create)


def main() -> None:
    """Run marimo edit with the authored notebook as the runtime filename."""

    _install_runtime_filename()

    from marimo._cli.cli import main as marimo_main

    marimo_main(prog_name="marimo")


if __name__ == "__main__":
    main()
