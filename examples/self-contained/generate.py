from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from notebook_markdown import export_notebook_markdown


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export a marimo notebook to output.md plus static media.",
    )
    parser.add_argument(
        "notebook", help="Path or marimo-resolvable notebook reference."
    )
    parser.add_argument(
        "output_dir",
        help="Directory that will receive output.md and media/.",
    )
    parser.add_argument(
        "--state-json",
        type=Path,
        help="JSON object with scenario state.",
    )
    parser.add_argument(
        "--scenario-id",
        default="default",
        help="Scenario id recorded in the generated Markdown.",
    )
    parser.add_argument(
        "--title",
        help="Markdown document title. Defaults to the notebook filename.",
    )
    parser.add_argument(
        "--to",
        type=Path,
        help="Optional static export root to keep for inspection.",
    )
    parser.add_argument(
        "--inline-html-bytes",
        type=int,
        default=16_384,
        help="Inline small HTML outputs. Larger or scripted HTML goes to media/.",
    )
    args = parser.parse_args()

    state, patches = _load_scenario(args.state_json)
    result = export_notebook_markdown(
        args.notebook,
        args.output_dir,
        scenario_id=args.scenario_id,
        state=state,
        patches=patches,
        to=args.to,
        title=args.title,
        inline_html_bytes=args.inline_html_bytes,
    )
    print(
        json.dumps(
            {
                "output": str(result.output_path),
                "media_dir": str(result.media_dir),
                "scenario": result.scenario_id,
                "bundle": result.bundle_id,
                "cells": result.cell_count,
                "outputs": result.output_count,
                "media_files": [str(path) for path in result.media_files],
            },
            indent=2,
        )
    )


def _load_scenario(path: Path | None) -> tuple[dict[str, Any], dict[str, Any]]:
    if path is None:
        return {}, {}

    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise TypeError("--state-json must point to a JSON object")
    state = parsed.get("state")
    patches = parsed.get("patches")
    if not isinstance(state, dict) or not isinstance(patches, dict):
        raise TypeError("--state-json must contain 'state' and 'patches' objects")
    return state, patches


if __name__ == "__main__":
    main()
