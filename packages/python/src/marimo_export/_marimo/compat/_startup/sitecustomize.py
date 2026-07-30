from __future__ import annotations

import os

if os.environ.get("MARIMO_EXPORT_MANAGED_CACHE_COMPAT") == "1":
    from marimo_export._marimo.compat.cache import (
        install_managed_cache_compat,
    )

    install_managed_cache_compat()
