from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import msgspec

_MANAGED_SOURCE_ENV = "MARIMO_EXPORT_MANAGED_SOURCE"
_MANAGED_SNAPSHOT_ENV = "MARIMO_EXPORT_MANAGED_SNAPSHOT"


def _cache_enabled_script_config(
    native: Callable[..., Any],
    manager: Any,
    *,
    hide_secrets: bool = True,
) -> Any:
    config = native(manager, hide_secrets=hide_secrets)
    runtime = dict(config.get("runtime", {}))
    runtime["cache_cells"] = True
    return {**config, "runtime": runtime}


def _install_runtime_filename() -> None:
    source = os.environ.pop(_MANAGED_SOURCE_ENV, None)
    snapshot = os.environ.pop(_MANAGED_SNAPSHOT_ENV, None)
    if source is None or snapshot is None:
        raise RuntimeError("managed notebook paths are unavailable")
    source_path = str(Path(source).resolve(strict=True))
    snapshot_path = str(Path(snapshot).resolve(strict=True))

    from marimo._runtime.commands import AppMetadata
    from marimo._session.session import SessionImpl

    native = SessionImpl.create

    def create(cls: type, **kwargs: Any) -> Any:
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
        return native(**kwargs)

    cast(Any, SessionImpl).create = classmethod(create)


def main() -> None:
    """Run marimo edit with native cell caching enabled for the owned session."""

    from marimo._config.manager import ScriptConfigManager

    native = ScriptConfigManager.get_config

    def get_config(
        manager: Any,
        *,
        hide_secrets: bool = True,
    ) -> Any:
        return _cache_enabled_script_config(
            native,
            manager,
            hide_secrets=hide_secrets,
        )

    cast(Any, ScriptConfigManager).get_config = get_config
    _install_runtime_filename()

    from marimo._cli.cli import main as marimo_main

    marimo_main(prog_name="marimo")


if __name__ == "__main__":
    main()
