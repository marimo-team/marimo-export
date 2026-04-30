"""Structured queries over static export roots."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from moexport.bundle.schema import BundleManifest, JsonObject, RootIndex, read_model
from moexport.query._helpers import (
    append_many_unique,
    append_unique,
    bundle_summary,
    catalog_formats,
    catalog_values,
    dedupe_export_files,
    exactly_one,
    matches_state,
    notebook_source_hash,
    resolve_export_root,
)
from moexport.query.bundle import BundleQuery


def open_export(path: str | Path) -> ExportQuery:
    """Open a static-export root, bundle directory, or manifest file."""

    return ExportQuery(resolve_export_root(Path(path)))


@dataclass(frozen=True)
class ExportQuery:
    """Query a `__marimo__/static-export` directory."""

    root: Path

    def bundles(self) -> list[JsonObject]:
        """List bundle summaries without expanding every artifact."""

        return [
            bundle_summary(manifest_path, read_model(manifest_path, BundleManifest))
            for manifest_path in self._manifest_paths()
        ]

    def root_index(self) -> JsonObject | None:
        """Return `index.json` for export roots that have one."""

        index_path = self.root / "index.json"
        if not index_path.exists():
            return None
        return read_model(index_path, RootIndex).model_dump(
            mode="json",
            by_alias=True,
        )

    def bundle(self, id: str | None = None) -> BundleQuery:
        """Open one bundle by id or id prefix.

        If the export root contains exactly one bundle, `id` can be omitted.
        """

        manifests = self._manifest_paths()
        if not manifests:
            raise FileNotFoundError(f"no export bundles found in {self.root}")

        if id is None:
            if len(manifests) == 1:
                return BundleQuery.from_manifest(self.root, manifests[0])
            ids = [path.parent.name for path in manifests]
            raise ValueError(f"multiple bundles found; choose one of {ids}")

        matches = [path for path in manifests if path.parent.name.startswith(id)]
        if not matches:
            raise KeyError(f"bundle {id!r} not found in {self.root}")
        if len(matches) > 1:
            ids = [path.parent.name for path in matches]
            raise ValueError(f"bundle id prefix {id!r} is ambiguous: {ids}")
        return BundleQuery.from_manifest(self.root, matches[0])

    def catalog(self) -> JsonObject:
        """Return a compact semantic index over the whole export root."""

        bundles = self._bundle_queries()
        scenarios = self.scenarios()
        artifacts = self.artifacts()
        files = self.files(dedupe=True)

        return {
            "root": str(self.root),
            "counts": {
                "bundles": len(bundles),
                "notebooks": len(self.notebooks()),
                "scenarios": len(scenarios),
                "values": len({row["name"] for row in self.values()}),
                "formats": len(self.formats()),
                "artifacts": len(artifacts),
                "files": len(files),
                "bytes": sum(
                    int(file["size"])
                    for file in files
                    if isinstance(file.get("size"), int)
                ),
            },
            "bundles": [bundle.summary() for bundle in bundles],
            "notebooks": self.notebooks(),
            "values": catalog_values(self.values()),
            "formats": self.formats(),
            "state_keys": sorted(
                {
                    key
                    for row in scenarios
                    for key in row.get("state", {})
                    if isinstance(row.get("state"), dict)
                }
            ),
            "media_types": sorted(
                {
                    str(row["media_type"])
                    for row in artifacts
                    if row.get("media_type") is not None
                }
            ),
            "scenarios": scenarios,
        }

    def notebooks(self) -> list[JsonObject]:
        """List notebooks represented by the export root."""

        by_key: dict[tuple[object, object], JsonObject] = {}
        for bundle in self._bundle_queries():
            notebook = bundle.manifest.notebook.model_dump(mode="json")
            key = (notebook_source_hash(notebook), notebook.get("name"))
            row = by_key.setdefault(
                key,
                {
                    **copy.deepcopy(notebook),
                    "bundles": [],
                    "exports": [],
                    "values": [],
                    "scenario_count": 0,
                },
            )
            append_unique(row["bundles"], bundle.id)
            append_unique(row["exports"], bundle.manifest.export.id)
            append_many_unique(row["values"], sorted(bundle.manifest.values))
            row["scenario_count"] += len(bundle.manifest.scenarios)

        return sorted(
            by_key.values(),
            key=lambda row: (
                str(row.get("name")),
                str(notebook_source_hash(row)),
            ),
        )

    def notebook_sources(
        self,
        *,
        bundle: str | None = None,
        scenario: str | None = None,
        state: Mapping[str, Any] | None = None,
    ) -> list[JsonObject]:
        """List stored notebook source blobs for matching scenarios."""

        by_key: dict[tuple[object, object], JsonObject] = {}
        for bundle_query in self._bundle_queries(bundle):
            scenarios = [
                row
                for row in bundle_query.scenarios(scenario=scenario)
                if matches_state(row.get("state"), state)
            ]
            if not scenarios:
                continue

            source = bundle_query.notebook_source()
            key = (notebook_source_hash(source), source.get("name"))
            row = by_key.setdefault(
                key,
                {
                    **source,
                    "bundles": [],
                    "scenarios": [],
                },
            )
            append_unique(row["bundles"], bundle_query.id)
            append_many_unique(
                row["scenarios"],
                [str(scenario_row["id"]) for scenario_row in scenarios],
            )

        return sorted(
            by_key.values(),
            key=lambda row: (
                str(row.get("name")),
                str(notebook_source_hash(row)),
            ),
        )

    def notebook_source(
        self,
        *,
        bundle: str | None = None,
        scenario: str | None = None,
        state: Mapping[str, Any] | None = None,
    ) -> JsonObject:
        """Return exactly one stored notebook source, including its text."""

        sources = self.notebook_sources(
            bundle=bundle,
            scenario=scenario,
            state=state,
        )
        if not sources:
            raise FileNotFoundError("no notebook source matched the query")
        if len(sources) > 1:
            labels = [
                f"{source.get('name')}:{notebook_source_hash(source)}"
                for source in sources
            ]
            raise ValueError(
                f"multiple notebook sources matched; narrow query: {labels}"
            )

        source = copy.deepcopy(sources[0])
        path = source.get("path")
        if not isinstance(path, str) or not source.get("exists"):
            raise FileNotFoundError(
                f"notebook source blob is missing for {source.get('name')!r}"
            )

        source["text"] = Path(path).read_text(encoding="utf-8")
        return source

    def scenarios(
        self,
        *,
        bundle: str | None = None,
        scenario: str | None = None,
        state: Mapping[str, Any] | None = None,
        value: str | None = None,
    ) -> list[JsonObject]:
        """List scenario rows across bundles."""

        rows = [
            row
            for bundle_query in self._bundle_queries(bundle)
            for row in bundle_query.scenarios(scenario=scenario, value=value)
        ]
        return [row for row in rows if matches_state(row.get("state"), state)]

    def values(
        self,
        *,
        bundle: str | None = None,
        value: str | None = None,
    ) -> list[JsonObject]:
        """List exported value specs across bundles."""

        return [
            row
            for bundle_query in self._bundle_queries(bundle)
            for row in bundle_query.values(value=value)
        ]

    def formats(
        self,
        *,
        bundle: str | None = None,
        scenario: str | None = None,
        state: Mapping[str, Any] | None = None,
        value: str | None = None,
        format: str | None = None,
    ) -> list[JsonObject]:
        """List format availability grouped by value and format name."""

        artifacts = self.artifacts(
            bundle=bundle,
            scenario=scenario,
            state=state,
            value=value,
            format=format,
        )
        return catalog_formats(artifacts)

    def artifacts(
        self,
        *,
        bundle: str | None = None,
        scenario: str | None = None,
        state: Mapping[str, Any] | None = None,
        value: str | None = None,
        format: str | None = None,
        format_id: str | None = None,
        media_type: str | None = None,
        limit: int | None = None,
    ) -> list[JsonObject]:
        """List artifact descriptors across bundles with structured filters."""

        rows = [
            artifact
            for bundle_query in self._bundle_queries(bundle)
            for artifact in bundle_query.artifacts(
                scenario=scenario,
                state=state,
                value=value,
                format=format,
                format_id=format_id,
                media_type=media_type,
            )
        ]
        return rows if limit is None else rows[:limit]

    def artifact(
        self,
        *,
        bundle: str | None = None,
        scenario: str | None = None,
        state: Mapping[str, Any] | None = None,
        value: str | None = None,
        format: str | None = None,
        format_id: str | None = None,
        media_type: str | None = None,
    ) -> JsonObject:
        """Return exactly one artifact across the export root."""

        return exactly_one(
            self.artifacts(
                bundle=bundle,
                scenario=scenario,
                state=state,
                value=value,
                format=format,
                format_id=format_id,
                media_type=media_type,
            ),
            "artifact",
        )

    def files(
        self,
        *,
        bundle: str | None = None,
        scenario: str | None = None,
        state: Mapping[str, Any] | None = None,
        value: str | None = None,
        format: str | None = None,
        format_id: str | None = None,
        media_type: str | None = None,
        dedupe: bool = True,
        limit: int | None = None,
    ) -> list[JsonObject]:
        """List raw blob files across bundles with semantic usage metadata."""

        rows: list[JsonObject] = []
        for artifact in self.artifacts(
            bundle=bundle,
            scenario=scenario,
            state=state,
            value=value,
            format=format,
            format_id=format_id,
            media_type=media_type,
        ):
            for file_key, file_record in artifact["files"].items():
                rows.append(
                    {
                        "bundle": artifact["bundle"],
                        "scenario": artifact["scenario"],
                        "state": copy.deepcopy(artifact["state"]),
                        "value": artifact["value"],
                        "source": artifact["source"],
                        "format": artifact["format"],
                        "format_id": artifact["format_id"],
                        "media_type": artifact["media_type"],
                        "file": file_key,
                        **copy.deepcopy(file_record),
                    }
                )

        if dedupe:
            rows = dedupe_export_files(rows)
        return rows if limit is None else rows[:limit]

    def file(
        self,
        *,
        bundle: str | None = None,
        scenario: str | None = None,
        state: Mapping[str, Any] | None = None,
        value: str | None = None,
        format: str | None = None,
        format_id: str | None = None,
        media_type: str | None = None,
        dedupe: bool = True,
    ) -> JsonObject:
        """Return exactly one blob file across the export root."""

        return exactly_one(
            self.files(
                bundle=bundle,
                scenario=scenario,
                state=state,
                value=value,
                format=format,
                format_id=format_id,
                media_type=media_type,
                dedupe=dedupe,
            ),
            "file",
        )

    def entries(
        self,
        *,
        bundle: str | None = None,
        scenario: str | None = None,
        state: Mapping[str, Any] | None = None,
        value: str | None = None,
        format: str | None = None,
        format_id: str | None = None,
        media_type: str | None = None,
        include_content: bool = False,
        max_bytes: int = 65_536,
        limit: int | None = None,
    ) -> list[JsonObject]:
        """List artifact entry files across bundles."""

        rows = [
            entry
            for bundle_query in self._bundle_queries(bundle)
            for entry in bundle_query.entries(
                scenario=scenario,
                state=state,
                value=value,
                format=format,
                format_id=format_id,
                media_type=media_type,
                include_content=include_content,
                max_bytes=max_bytes,
            )
        ]
        return rows if limit is None else rows[:limit]

    def entry(
        self,
        *,
        bundle: str | None = None,
        scenario: str | None = None,
        state: Mapping[str, Any] | None = None,
        value: str | None = None,
        format: str | None = None,
        format_id: str | None = None,
        media_type: str | None = None,
        include_content: bool = False,
        max_bytes: int = 65_536,
    ) -> JsonObject:
        """Return exactly one artifact entry file across the export root."""

        return exactly_one(
            self.entries(
                bundle=bundle,
                scenario=scenario,
                state=state,
                value=value,
                format=format,
                format_id=format_id,
                media_type=media_type,
                include_content=include_content,
                max_bytes=max_bytes,
            ),
            "entry",
        )

    def _manifest_paths(self) -> list[Path]:
        index_path = self.root / "index.json"
        if index_path.exists():
            index = read_model(index_path, RootIndex)
            paths = [self.root / bundle.manifest_href for bundle in index.bundles]
            existing = [path for path in paths if path.exists()]
            if existing:
                return existing

        return sorted((self.root / "bundles").glob("*/manifest.json"))

    def _bundle_queries(self, id: str | None = None) -> list[BundleQuery]:
        if id is not None:
            return [self.bundle(id)]
        return [
            BundleQuery.from_manifest(self.root, manifest_path)
            for manifest_path in self._manifest_paths()
        ]
