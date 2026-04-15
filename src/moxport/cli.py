from __future__ import annotations

import argparse
from collections.abc import Sequence

from .client import MarimoClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lean typed marimo HTTP client")
    parser.add_argument("--server", required=True, help="Base URL of the marimo server")
    parser.add_argument("--notebook", help="Notebook filename/path/stem to resolve")
    parser.add_argument(
        "--session-id", help="Attach directly to an active marimo session id"
    )
    parser.add_argument("--token", help="Optional bearer token")
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with MarimoClient(token=args.token) as client:
        notebook = client.connect(
            args.server,
            notebook_name=args.notebook,
            session_id=args.session_id,
        )
        summary = notebook.summary()
    if args.json:
        print(summary.model_dump_json(indent=2))
    else:
        print(f"session_id: {summary.session.session_id}")
        print(f"path: {summary.session.path or summary.session.filename}")
        print(f"cell_count: {summary.cell_count}")
    return 0
