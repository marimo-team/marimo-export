from __future__ import annotations

import json
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from moexport.client._types import SpecInput
from moexport.spec import parse_export_spec


def export_code(
    spec: SpecInput,
    *,
    to: str | Path | None,
    paths: Iterable[str | Path],
    marker: str,
) -> str:
    spec_json = spec_json_string(spec)
    to_expression = "None" if to is None else json.dumps(str(to))
    return "\n".join(
        [
            "import json",
            *_path_code(paths),
            "import importlib",
            "import moexport as mox",
            "mox = importlib.reload(mox)",
            f"__moexport_spec = json.loads({json.dumps(spec_json)})",
            f"__moexport_result = await mox.capture(__moexport_spec, to={to_expression})",
            "__moexport_payload = {",
            '    "bundle_path": __moexport_result.bundle_path,',
            '    "manifest_path": __moexport_result.manifest_path,',
            '    "invocation_path": __moexport_result.invocation_path,',
            '    "invocation_index_path": __moexport_result.invocation_index_path,',
            '    "manifest": __moexport_result.manifest,',
            '    "invocation": __moexport_result.invocation,',
            "}",
            f"print({json.dumps(marker)} + json.dumps(__moexport_payload, allow_nan=False))",
        ]
    )


def archive_code(
    spec: SpecInput,
    *,
    paths: Iterable[str | Path],
    marker: str,
) -> str:
    spec_json = spec_json_string(spec)
    return "\n".join(
        [
            "import json",
            *_path_code(paths),
            "import importlib",
            "import moexport.archive as __moexport_archive",
            "__moexport_archive = importlib.reload(__moexport_archive)",
            f"__moexport_spec = json.loads({json.dumps(spec_json)})",
            f"await __moexport_archive.emit_bundle_archive(__moexport_spec, marker={json.dumps(marker)})",
        ]
    )


def marked_payload(stdout: list[str], marker: str) -> dict[str, Any]:
    payload = marked_text(stdout, marker)
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise RuntimeError("export payload was not a JSON object")
    return data


def marked_text(stdout: list[str], marker: str) -> str:
    for line in reversed(stdout):
        if line.startswith(marker):
            return line[len(marker) :]
    raise RuntimeError("marimo scratchpad output did not include the export marker")


def marker(kind: str) -> str:
    return f"__MOEXPORT_{kind}_{time.time_ns()}__"


def spec_json_string(spec: SpecInput) -> str:
    normalized = parse_export_spec(spec).model_dump(mode="json", exclude_none=True)
    return json.dumps(normalized, allow_nan=False)


def _path_code(paths: Iterable[str | Path]) -> list[str]:
    values = [str(path) for path in paths]
    if not values:
        return []
    return [
        "import importlib",
        "import sys",
        f"for __moexport_path in {json.dumps(values)}:",
        "    if __moexport_path not in sys.path:",
        "        sys.path.insert(0, __moexport_path)",
        "importlib.invalidate_caches()",
    ]
