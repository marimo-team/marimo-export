"""Structured queries over one static export bundle."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from moexport.blobs import BlobRef, validate_bundle_href
from moexport.bundle.schema import (
    BundleManifest,
    InvocationIndex,
    InvocationRecord,
    JsonObject,
    ManifestArtifact,
    ManifestScenario,
    read_model,
)
from moexport.query._helpers import (
    bundle_summary,
    dedupe_bundle_files,
    exactly_one,
    matches_state,
    matches_scalar,
)


@dataclass(frozen=True)
class BundleQuery:
    """Query one materialized export bundle."""

    root: Path
    path: Path
    manifest_path: Path
    manifest: BundleManifest

    @classmethod
    def from_manifest(cls, root: Path, manifest_path: Path) -> BundleQuery:
        return cls(
            root=root,
            path=manifest_path.parent,
            manifest_path=manifest_path,
            manifest=read_model(manifest_path, BundleManifest),
        )

    @property
    def id(self) -> str:
        return self.manifest.id

    def summary(self) -> JsonObject:
        """Return a compact bundle summary."""

        return bundle_summary(self.manifest_path, self.manifest)

    def map(self) -> JsonObject:
        """Return a compact semantic map for bundle inspection."""

        return {
            **self.summary(),
            "values": self.values(),
            "scenarios": self.scenarios(),
            "artifacts": self.artifacts(),
            "files": self.files(dedupe=True),
            "traces": self.traces(),
        }

    def values(self, *, value: str | None = None) -> list[JsonObject]:
        """List exported values and their authored source expressions."""

        rows = [
            {
                "bundle": self.id,
                "bundle_path": str(self.path),
                "notebook": self.manifest.notebook.model_dump(mode="json"),
                "name": name,
                **record.model_dump(mode="json"),
            }
            for name, record in self.manifest.values.items()
        ]
        return [row for row in rows if matches_scalar(row["name"], value)]

    def notebook_source(self) -> JsonObject:
        """Return the notebook source blob reference resolved to the filesystem."""

        notebook = self.manifest.notebook.model_dump(mode="json")
        source = self.manifest.notebook.source
        if source is None:
            return {
                **notebook,
                "path": None,
                "exists": False,
            }

        path = self.resolve(source)
        return {
            **notebook,
            "path": str(path),
            "exists": path.exists(),
        }

    def scenarios(
        self,
        *,
        scenario: str | None = None,
        value: str | None = None,
    ) -> list[JsonObject]:
        """List scenarios and the value/format matrix available in each."""

        rows = []
        for scenario_record in self._scenario_records(scenario):
            values = {
                value_name: sorted(formats)
                for value_name, formats in scenario_record.values.items()
                if matches_scalar(value_name, value)
            }
            if value is not None and not values:
                continue
            row = {
                "bundle": self.id,
                "bundle_path": str(self.path),
                "notebook": self.manifest.notebook.model_dump(mode="json"),
                "id": scenario_record.id,
                "state": copy.deepcopy(scenario_record.state),
                "values": values,
                "artifact_count": sum(len(formats) for formats in values.values()),
            }
            if scenario_record.declared_state is not None:
                row["declared_state"] = copy.deepcopy(scenario_record.declared_state)
            rows.append(row)
        return rows

    def artifacts(
        self,
        *,
        scenario: str | None = None,
        state: Mapping[str, Any] | None = None,
        value: str | None = None,
        format: str | None = None,
        format_id: str | None = None,
        media_type: str | None = None,
    ) -> list[JsonObject]:
        """Flatten artifact descriptors with scenario/value/format metadata."""

        rows: list[JsonObject] = []
        for scenario_record in self._scenario_records(scenario):
            if not matches_state(scenario_record.state, state):
                continue
            for value_name, formats in scenario_record.values.items():
                if not matches_scalar(value_name, value):
                    continue
                value_spec = self.manifest.values.get(value_name)
                source = value_spec.source if value_spec is not None else None
                for format_name, artifact in formats.items():
                    if (
                        not matches_scalar(format_name, format)
                        or not matches_scalar(artifact.format_id, format_id)
                        or not matches_scalar(artifact.media_type, media_type)
                    ):
                        continue
                    rows.append(
                        self._artifact_record(
                            scenario_record=scenario_record,
                            value_name=value_name,
                            source=source,
                            format_name=format_name,
                            artifact=artifact,
                        )
                    )
        return rows

    def artifact(
        self,
        *,
        scenario: str | None = None,
        state: Mapping[str, Any] | None = None,
        value: str | None = None,
        format: str | None = None,
        format_id: str | None = None,
        media_type: str | None = None,
    ) -> JsonObject:
        """Return exactly one artifact, failing on empty or ambiguous selectors."""

        return exactly_one(
            self.artifacts(
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
        scenario: str | None = None,
        state: Mapping[str, Any] | None = None,
        value: str | None = None,
        format: str | None = None,
        format_id: str | None = None,
        media_type: str | None = None,
        dedupe: bool = False,
    ) -> list[JsonObject]:
        """Flatten blob files, optionally deduped by href with usage records."""

        rows: list[JsonObject] = []
        for artifact in self.artifacts(
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

        return dedupe_bundle_files(rows) if dedupe else rows

    def file(
        self,
        *,
        scenario: str | None = None,
        state: Mapping[str, Any] | None = None,
        value: str | None = None,
        format: str | None = None,
        format_id: str | None = None,
        media_type: str | None = None,
        dedupe: bool = False,
    ) -> JsonObject:
        """Return exactly one blob file row for a structured selector."""

        return exactly_one(
            self.files(
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
        scenario: str | None = None,
        state: Mapping[str, Any] | None = None,
        value: str | None = None,
        format: str | None = None,
        format_id: str | None = None,
        media_type: str | None = None,
        include_content: bool = False,
        max_bytes: int = 65_536,
    ) -> list[JsonObject]:
        """List each artifact's canonical entry file.

        This is still loader-free: it resolves the artifact entry file and can
        optionally inline small JSON/text content for agent-facing inspection.
        """

        rows: list[JsonObject] = []
        for artifact in self.artifacts(
            scenario=scenario,
            state=state,
            value=value,
            format=format,
            format_id=format_id,
            media_type=media_type,
        ):
            entry = artifact.get("entry")
            if not isinstance(entry, str):
                continue
            files = artifact.get("files")
            if not isinstance(files, Mapping):
                continue
            file_record = files.get(entry)
            if not isinstance(file_record, Mapping):
                continue

            row: JsonObject = {
                "bundle": artifact["bundle"],
                "scenario": artifact["scenario"],
                "state": copy.deepcopy(artifact["state"]),
                "value": artifact["value"],
                "source": artifact["source"],
                "format": artifact["format"],
                "format_id": artifact["format_id"],
                "artifact_media_type": artifact["media_type"],
                "metadata": copy.deepcopy(artifact["metadata"]),
                "entry": entry,
                **copy.deepcopy(file_record),
            }
            if include_content:
                row["content"] = _entry_content(
                    row.get("path"),
                    media_type=row.get("media_type"),
                    max_bytes=max_bytes,
                )
            rows.append(row)
        return rows

    def entry(
        self,
        *,
        scenario: str | None = None,
        state: Mapping[str, Any] | None = None,
        value: str | None = None,
        format: str | None = None,
        format_id: str | None = None,
        media_type: str | None = None,
        include_content: bool = False,
        max_bytes: int = 65_536,
    ) -> JsonObject:
        """Return exactly one artifact entry file."""

        return exactly_one(
            self.entries(
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

    def traces(self) -> list[JsonObject]:
        """List invocation trace records without loading their full payloads."""

        index_path = self._trace_index_path()
        if not index_path.exists():
            return []

        return [
            {
                **invocation.model_dump(mode="json"),
                "path": str(self.resolve(invocation.href)),
            }
            for invocation in read_model(index_path, InvocationIndex).invocations
        ]

    def trace(
        self,
        scenario: str | None = None,
        *,
        invocation: str | None = None,
    ) -> JsonObject:
        """Load the latest invocation trace, optionally scoped to a scenario."""

        trace_record = read_model(self._trace_path(invocation), InvocationRecord)
        if scenario is None:
            return trace_record.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            )

        for scenario_record in trace_record.scenarios:
            if scenario_record.id == scenario:
                return scenario_record.model_dump(mode="json", exclude_none=True)
        raise KeyError(f"scenario {scenario!r} not found in trace")

    def graph(
        self,
        scenario: str | None = None,
        *,
        invocation: str | None = None,
    ) -> JsonObject:
        """Return trace graph metadata for notebook/layout inspection."""

        if scenario is not None:
            trace = self.trace(scenario, invocation=invocation).get("trace", {})
            graph = trace.get("graph", {}) if isinstance(trace, dict) else {}
            return copy.deepcopy(graph if isinstance(graph, dict) else {})

        graphs: JsonObject = {}
        for scenario_record in self.trace(invocation=invocation).get("scenarios", []):
            if not isinstance(scenario_record, dict):
                continue
            trace = scenario_record.get("trace", {})
            graph = trace.get("graph", {}) if isinstance(trace, dict) else {}
            graphs[str(scenario_record["id"])] = copy.deepcopy(graph)
        return graphs

    def resolve(self, href_or_ref: str | BlobRef | dict[str, Any]) -> Path:
        """Resolve a root-relative href or blob ref to a local path."""

        if isinstance(href_or_ref, BlobRef):
            href = href_or_ref.href
        else:
            href = href_or_ref["href"] if isinstance(href_or_ref, dict) else href_or_ref
        return self.root / validate_bundle_href(str(href))

    def _scenario_records(self, scenario: str | None) -> list[ManifestScenario]:
        if scenario is None:
            return self.manifest.scenarios

        matches = [item for item in self.manifest.scenarios if item.id == scenario]
        if not matches:
            raise KeyError(f"scenario {scenario!r} not found in bundle {self.id}")
        return matches

    def _artifact_record(
        self,
        *,
        scenario_record: ManifestScenario,
        value_name: str,
        source: object,
        format_name: str,
        artifact: ManifestArtifact,
    ) -> JsonObject:
        files = {
            file_key: {
                **file_ref.model_dump(mode="json"),
                "path": str(self.resolve(file_ref)),
                "exists": self.resolve(file_ref).exists(),
            }
            for file_key, file_ref in artifact.data.files.items()
        }
        entry = artifact.data.entry
        entry_path = (
            files.get(entry, {}).get("path") if isinstance(entry, str) else None
        )
        return {
            "bundle": self.id,
            "bundle_path": str(self.path),
            "manifest_path": str(self.manifest_path),
            "notebook": self.manifest.notebook.model_dump(mode="json"),
            "scenario": scenario_record.id,
            "state": copy.deepcopy(scenario_record.state),
            "value": value_name,
            "source": source,
            "format": format_name,
            "format_id": artifact.format_id,
            "media_type": artifact.media_type,
            "metadata": copy.deepcopy(artifact.metadata),
            "data": artifact.data.model_dump(mode="json"),
            "files": files,
            "entry": entry,
            "entry_path": entry_path,
        }

    def _trace_index_path(self) -> Path:
        href = self.manifest.provenance.invocations_index_href
        return (
            self.resolve(href)
            if href is not None
            else self.path / "traces" / "index.json"
        )

    def _trace_path(self, invocation: str | None) -> Path:
        traces = self.traces()
        if not traces:
            raise FileNotFoundError(f"no invocation traces found for bundle {self.id}")

        if invocation is None:
            return Path(str(traces[-1]["path"]))

        matches = [
            trace
            for trace in traces
            if str(trace["id"]).startswith(invocation)
            or str(trace["sha256"]).startswith(invocation)
        ]
        if not matches:
            raise KeyError(f"invocation {invocation!r} not found for bundle {self.id}")
        if len(matches) > 1:
            ids = [trace["id"] for trace in matches]
            raise ValueError(f"invocation prefix {invocation!r} is ambiguous: {ids}")
        return Path(str(matches[0]["path"]))


def _entry_content(
    path: object,
    *,
    media_type: object,
    max_bytes: int,
) -> JsonObject:
    if not isinstance(path, str):
        return {"type": "missing", "reason": "entry file has no resolved path"}

    file_path = Path(path)
    if not file_path.exists():
        return {"type": "missing", "path": path}

    size = file_path.stat().st_size
    if size > max_bytes:
        return {
            "type": "omitted",
            "reason": f"entry file is {size} bytes. Max is {max_bytes}",
            "size": size,
        }

    raw = file_path.read_bytes()
    media = str(media_type or "")
    if _is_json_media(media, file_path):
        try:
            text = raw.decode("utf-8")
            return {"type": "json", "value": json.loads(text)}
        except UnicodeDecodeError as exc:
            return {
                "type": "invalid-text",
                "reason": str(exc),
                "size": size,
            }
        except json.JSONDecodeError as exc:
            return {
                "type": "invalid-json",
                "reason": str(exc),
                "text": text,
            }

    if _is_text_media(media, file_path):
        try:
            return {"type": "text", "text": raw.decode("utf-8")}
        except UnicodeDecodeError as exc:
            return {
                "type": "invalid-text",
                "reason": str(exc),
                "size": size,
            }

    return {
        "type": "binary",
        "reason": f"entry media type is {media or 'unknown'}",
        "size": size,
    }


def _is_json_media(media_type: str, path: Path) -> bool:
    return (
        media_type == "application/json"
        or media_type.endswith("+json")
        or path.suffix == ".json"
    )


def _is_text_media(media_type: str, path: Path) -> bool:
    return (
        media_type.startswith("text/")
        or media_type in {"image/svg+xml", "application/xml"}
        or path.suffix in {".txt", ".html", ".htm", ".svg", ".xml"}
    )
