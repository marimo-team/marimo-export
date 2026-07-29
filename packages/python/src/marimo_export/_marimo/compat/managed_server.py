from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast


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

    from marimo._cli.cli import main as marimo_main

    marimo_main(prog_name="marimo")


if __name__ == "__main__":
    main()
