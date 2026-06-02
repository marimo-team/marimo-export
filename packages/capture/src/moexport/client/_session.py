from __future__ import annotations

from typing import Any

from moexport.client._http import post_json


def resolve_session(
    *,
    server: str,
    notebook: str | None,
    session_id: str | None,
    token: str | None,
) -> dict[str, Any]:
    """Resolve one running marimo session for `notebook`."""

    if session_id:
        return {
            "sessionId": session_id,
            "name": None,
            "path": notebook,
            "initializationId": None,
        }

    response = post_json(
        server,
        "/api/home/running_notebooks",
        body=None,
        token=token,
        headers=None,
        timeout=30,
    )
    files = response.get("files")
    if not isinstance(files, list):
        raise RuntimeError("marimo did not return a running notebook list")

    matches = [
        item
        for item in files
        if isinstance(item, dict)
        and item.get("sessionId")
        and (not notebook or notebook_matches(item, notebook))
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        available = ", ".join(
            str(item.get("path") or item.get("name")) for item in files
        )
        raise RuntimeError(
            f"No running notebook matched {notebook!r}. Available: {available}"
        )

    available = ", ".join(str(item.get("path") or item.get("name")) for item in matches)
    raise RuntimeError(
        f"More than one running notebook matched {notebook!r}: {available}"
    )


def notebook_matches(item: dict[str, Any], query: str) -> bool:
    """Return whether a running notebook record matches a path or filename."""

    path = str(item.get("path") or "")
    name = str(item.get("name") or "")
    return path == query or name == query or path.endswith(f"/{query}")
