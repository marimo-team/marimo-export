# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pyyaml>=6,<7",
# ]
# ///
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, TypeAlias

import yaml

JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)


def _json_value(value: Any, source: Path) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list):
        return [_json_value(item, source) for item in value]
    if isinstance(value, dict):
        output: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(
                    f"{source}: YAML mapping key is not a string: {key!r}"
                )
            output[key] = _json_value(item, source)
        return output
    raise TypeError(f"{source}: value is not JSON-compatible: {value!r}")


def _yaml_paths(source_dir: Path) -> list[Path]:
    return sorted(
        path
        for suffix in ("*.yaml", "*.yml")
        for path in source_dir.glob(suffix)
        if path.is_file()
    )


def sync_specs(source_dir: Path, output_dir: Path) -> list[Path]:
    if not source_dir.is_dir():
        raise FileNotFoundError(f"YAML spec directory does not exist: {source_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for yaml_path in _yaml_paths(source_dir):
        data = _json_value(
            yaml.safe_load(yaml_path.read_text(encoding="utf-8")),
            yaml_path,
        )
        json_path = output_dir / f"{yaml_path.stem}.json"
        payload = json.dumps(data, allow_nan=False, indent=2)
        json_path.write_text(f"{payload}\n", encoding="utf-8")
        written.append(json_path)

    return written


def main() -> None:
    repo_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Convert notebook export specs from YAML to deterministic JSON."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=repo_dir / "notebooks" / "export-specs" / "yaml",
        help="Directory containing .yaml/.yml export specs.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repo_dir / "notebooks" / "export-specs" / "json",
        help="Directory where .json export specs are written.",
    )
    args = parser.parse_args()

    written = sync_specs(args.source, args.output)
    for path in written:
        print(path)


if __name__ == "__main__":
    main()
