from __future__ import annotations

import time

from moexport.client._http import post_json
from moexport.client._scratchpad import can_import


def ensure_runtime(
    *,
    server: str,
    session_id: str,
    package: str,
    force: bool,
    token: str | None,
    module: str = "moexport",
) -> None:
    """Install `package` into a kernel when `module` is unavailable."""

    if not force and can_import(server, session_id, module, token=token):
        return

    post_json(
        server,
        "/api/kernel/install_missing_packages",
        body={
            "manager": "uv",
            "source": "kernel",
            "versions": {package: ""},
        },
        headers={"Marimo-Session-Id": session_id},
        token=token,
        timeout=30,
    )
    deadline = time.time() + 120
    while time.time() < deadline:
        if can_import(server, session_id, module, token=token):
            return
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting for {module} in the kernel")
