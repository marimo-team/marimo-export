from __future__ import annotations

import json
from collections.abc import Iterable

from .errors import ScratchpadProtocolError
from .models import ScratchpadError, ScratchpadResult


def normalize_stream_line(line: str | bytes) -> str:
    return line.decode() if isinstance(line, bytes) else line


def parse_execute_stream(lines: Iterable[str | bytes]) -> ScratchpadResult:
    stdout: list[str] = []
    stderr: list[str] = []
    current_event: str | None = None
    done: dict[str, object] | None = None

    for raw_line in lines:
        line = normalize_stream_line(raw_line)
        if not line:
            continue
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
            continue
        if not line.startswith("data:"):
            continue

        payload = json.loads(line.split(":", 1)[1].strip())
        if current_event == "stdout":
            stdout.append(str(payload.get("data", "")))
        elif current_event == "stderr":
            stderr.append(str(payload.get("data", "")))
        elif current_event == "done":
            if isinstance(payload, dict):
                done = payload

    if done is None:
        raise ScratchpadProtocolError("Scratchpad stream ended without a done event")

    if bool(done.get("success")):
        output = done.get("output")
        if isinstance(output, dict):
            output_data = output.get("data")
            mimetype = output.get("mimetype")
        else:
            output_data = None
            mimetype = None
        return ScratchpadResult(
            success=True,
            stdout="".join(stdout),
            stderr="".join(stderr),
            output=output_data,
            output_mimetype=str(mimetype) if mimetype is not None else None,
        )

    error = done.get("error")
    if isinstance(error, dict):
        error_model = ScratchpadError.model_validate(error)
    else:
        error_model = None
    return ScratchpadResult(
        success=False,
        stdout="".join(stdout),
        stderr="".join(stderr),
        error=error_model,
    )
