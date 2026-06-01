"""Pure helpers for query rows, filters, and path resolution."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from moexport.bundle.schema import BundleManifest, JsonObject


def bundle_summary(manifest_path: Path, manifest: BundleManifest) -> JsonObject:
    return {
        "id": manifest.id,
        "sha256": manifest.sha256,
        "path": str(manifest_path.parent),
        "manifest_path": str(manifest_path),
        "notebook": manifest.notebook.model_dump(mode="json"),
        "export": manifest.export.model_dump(mode="json"),
        "value_count": len(manifest.values),
        "scenario_count": len(manifest.scenarios),
        "values": sorted(manifest.values),
        "scenarios": [scenario.id for scenario in manifest.scenarios],
    }


def catalog_values(rows: list[JsonObject]) -> list[JsonObject]:
    by_name: dict[str, JsonObject] = {}
    for row in rows:
        name = str(row["name"])
        record = by_name.setdefault(
            name,
            {"name": name, "sources": [], "formats": [], "bundles": []},
        )
        append_unique(record["sources"], row.get("source"))
        append_many_unique(record["formats"], row.get("formats", []))
        append_unique(record["bundles"], row.get("bundle"))
    return sorted(by_name.values(), key=lambda record: record["name"])


def catalog_formats(artifacts: list[JsonObject]) -> list[JsonObject]:
    by_key: dict[tuple[str, str], JsonObject] = {}
    for artifact in artifacts:
        key = (str(artifact["value"]), str(artifact["format"]))
        record = by_key.setdefault(
            key,
            {
                "value": artifact["value"],
                "format": artifact["format"],
                "format_ids": [],
                "media_types": [],
                "bundles": [],
                "scenarios": [],
            },
        )
        append_unique(record["format_ids"], artifact.get("format_id"))
        append_unique(record["media_types"], artifact.get("media_type"))
        append_unique(record["bundles"], artifact.get("bundle"))
        append_unique(record["scenarios"], artifact.get("scenario"))

    return sorted(
        by_key.values(), key=lambda record: (record["value"], record["format"])
    )


def append_unique(items: list[Any], value: object) -> None:
    if value is not None and value not in items:
        items.append(value)


def append_many_unique(items: list[Any], values: object) -> None:
    if isinstance(values, list):
        for value in values:
            append_unique(items, value)


def matches_scalar(actual: object, expected: str | None) -> bool:
    return expected is None or actual == expected


def matches_state(
    state: object,
    expected: Mapping[str, Any] | None,
) -> bool:
    if not expected:
        return True
    if not isinstance(state, Mapping):
        return False
    actual = dict(state)
    return all(actual.get(key) == value for key, value in expected.items())


def exactly_one(rows: list[JsonObject], label: str) -> JsonObject:
    if not rows:
        raise FileNotFoundError(f"no {label} matched")
    if len(rows) > 1:
        raise ValueError(f"multiple {_plural(label)} matched. Narrow the query")
    return rows[0]


def _plural(label: str) -> str:
    if label.endswith("y"):
        return f"{label[:-1]}ies"
    if label.endswith("s"):
        return f"{label}es"
    return f"{label}s"


def notebook_source_hash(notebook: Mapping[str, Any]) -> object:
    source = notebook.get("source")
    return source.get("sha256") if isinstance(source, Mapping) else None


def dedupe_bundle_files(rows: list[JsonObject]) -> list[JsonObject]:
    return _dedupe_files(
        rows,
        usage_keys=(
            "scenario",
            "state",
            "value",
            "source",
            "format",
            "format_id",
            "file",
        ),
    )


def dedupe_export_files(rows: list[JsonObject]) -> list[JsonObject]:
    return _dedupe_files(
        rows,
        usage_keys=(
            "bundle",
            "scenario",
            "state",
            "value",
            "source",
            "format",
            "format_id",
            "file",
        ),
    )


def resolve_export_root(path: Path) -> Path:
    path = path.expanduser()
    if path.is_file() and path.name == "manifest.json":
        bundle_path = path.parent
        if bundle_path.parent.name == "bundles":
            return bundle_path.parent.parent

    if path.is_dir() and (path / "manifest.json").exists():
        if path.parent.name == "bundles":
            return path.parent.parent

    if path.is_dir() and (path / "bundles").is_dir():
        return path

    raise FileNotFoundError(
        f"expected a static-export root, bundle directory, or bundle manifest: {path}"
    )


def _dedupe_files(
    rows: list[JsonObject],
    *,
    usage_keys: tuple[str, ...],
) -> list[JsonObject]:
    by_href: dict[str, JsonObject] = {}
    for row in rows:
        href = str(row["href"])
        use = {key: copy.deepcopy(row[key]) for key in usage_keys}
        if href not in by_href:
            by_href[href] = {
                key: copy.deepcopy(item)
                for key, item in row.items()
                if key not in usage_keys
            }
            by_href[href]["uses"] = []
        by_href[href]["uses"].append(use)
    return list(by_href.values())
