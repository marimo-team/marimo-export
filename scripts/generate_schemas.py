from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path

from marimo_export.publication import publication_json_schema
from marimo_export.spec import spec_json_schema


def _content(schema: Mapping[str, object]) -> str:
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _schemas(root: Path) -> tuple[tuple[Path, str], ...]:
    return (
        (root / "schemas" / "spec.v1.json", _content(spec_json_schema())),
        (
            root / "schemas" / "publication.v1.json",
            _content(publication_json_schema()),
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate marimo-export JSON Schemas")
    parser.add_argument("--check", action="store_true", help="fail when committed schemas differ")
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]

    stale: list[Path] = []
    for path, expected in _schemas(root):
        if arguments.check:
            try:
                actual = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                actual = ""
            if actual != expected:
                stale.append(path.relative_to(root))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected, encoding="utf-8")

    if stale:
        parser.exit(1, f"stale generated schemas: {', '.join(map(str, stale))}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
