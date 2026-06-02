from __future__ import annotations

import math
import time

from moexport.client._http import post_json
from moexport.client._scratchpad import can_import


def ensure_runtime(
    *,
    server: str,
    session_id: str,
    package: str,
    manager: str,
    source: str,
    force: bool,
    token: str | None,
    module: str = "moexport",
    timeout_ms: int = 120_000,
    poll_interval_ms: int = 1_000,
) -> None:
    """Install `package` into a kernel when `module` is unavailable."""

    if timeout_ms <= 0:
        raise ValueError("runtime timeout_ms must be positive")
    if poll_interval_ms <= 0:
        raise ValueError("runtime poll_interval_ms must be positive")

    if not force and can_import(server, session_id, module, token=token):
        return

    post_json(
        server,
        "/api/kernel/install_missing_packages",
        body={
            "manager": manager,
            "source": source,
            "versions": {package: ""},
        },
        headers={"Marimo-Session-Id": session_id},
        token=token,
        timeout=max(1, math.ceil(timeout_ms / 1000)),
    )
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if can_import(server, session_id, module, token=token):
            return
        time.sleep(poll_interval_ms / 1000)
    raise TimeoutError(f"Timed out waiting for {module} in the kernel")
