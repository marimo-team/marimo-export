from __future__ import annotations

import json
import re
from dataclasses import dataclass

from marimo_export._json import (
    JsonObject,
    canonical_bytes,
    json_identity,
    json_object,
    sha256_bytes,
)

INDEX_SCHEMA = "marimo-export.index.v1"
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class PayloadRef:
    key: str
    sha256: str
    size: int

    def wire(self) -> JsonObject:
        return {"key": self.key, "sha256": self.sha256, "size": self.size}

    @classmethod
    def from_wire(cls, value: object) -> PayloadRef:
        item = json_object(value, "payload")
        _exact(item, {"key", "sha256", "size"}, "payload")
        digest = _digest(item.get("sha256"), "payload.sha256")
        key = _string(item.get("key"), "payload.key")
        expected = f"marimo-export/payloads/sha256/{digest}"
        if key != expected:
            raise ValueError(f"payload.key must be {expected!r}")
        size = _non_negative_integer(item.get("size"), "payload.size")
        return cls(key=key, sha256=digest, size=size)


@dataclass(frozen=True)
class ProjectionEntry:
    format_id: str
    media_type: str
    metadata: JsonObject
    payload: PayloadRef

    def wire(self) -> JsonObject:
        return {
            "format_id": self.format_id,
            "media_type": self.media_type,
            "metadata": self.metadata,
            "payload": self.payload.wire(),
        }

    @classmethod
    def from_wire(cls, value: object) -> ProjectionEntry:
        item = json_object(value, "projection")
        _exact(
            item,
            {"format_id", "media_type", "metadata", "payload"},
            "projection",
        )
        return cls(
            format_id=_string(item.get("format_id"), "projection.format_id"),
            media_type=_string(item.get("media_type"), "projection.media_type"),
            metadata=json_object(item.get("metadata"), "projection.metadata"),
            payload=PayloadRef.from_wire(item.get("payload")),
        )


@dataclass(frozen=True)
class ScenarioIndex:
    id: str
    inputs: JsonObject
    outputs: dict[str, dict[str, ProjectionEntry]]

    def wire(self) -> JsonObject:
        return {
            "id": self.id,
            "inputs": self.inputs,
            "outputs": {
                output_name: {format_name: entry.wire() for format_name, entry in formats.items()}
                for output_name, formats in self.outputs.items()
            },
        }

    @classmethod
    def from_wire(cls, value: object) -> ScenarioIndex:
        item = json_object(value, "scenario")
        _exact(item, {"id", "inputs", "outputs"}, "scenario")
        inputs = json_object(item.get("inputs"), "scenario.inputs")
        raw_outputs = json_object(item.get("outputs"), "scenario.outputs")
        if not raw_outputs:
            raise ValueError("scenario.outputs must not be empty")
        outputs: dict[str, dict[str, ProjectionEntry]] = {}
        for output_name, raw_formats in raw_outputs.items():
            _string(output_name, "scenario.outputs key")
            format_map = json_object(raw_formats, f"scenario.outputs.{output_name}")
            if not format_map:
                raise ValueError(f"scenario.outputs.{output_name} must not be empty")
            parsed_formats: dict[str, ProjectionEntry] = {}
            for format_name, raw_entry in format_map.items():
                _string(format_name, f"scenario.outputs.{output_name} key")
                parsed_formats[format_name] = ProjectionEntry.from_wire(raw_entry)
            outputs[output_name] = parsed_formats
        return cls(
            id=_string(item.get("id"), "scenario.id"),
            inputs=inputs,
            outputs=outputs,
        )


@dataclass(frozen=True)
class ProducerInfo:
    marimo_version: str
    marimo_export_version: str

    def wire(self) -> JsonObject:
        return {
            "marimo_version": self.marimo_version,
            "marimo_export_version": self.marimo_export_version,
        }


@dataclass(frozen=True)
class ExportIndex:
    notebook_name: str
    notebook_source_sha256: str
    plan_sha256: str
    producer: ProducerInfo
    scenarios: tuple[ScenarioIndex, ...]

    def wire(self) -> JsonObject:
        return {
            "schema": INDEX_SCHEMA,
            "notebook": {
                "name": self.notebook_name,
                "source_sha256": self.notebook_source_sha256,
            },
            "plan_sha256": self.plan_sha256,
            "producer": self.producer.wire(),
            "scenarios": [scenario.wire() for scenario in self.scenarios],
        }

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.wire())

    @classmethod
    def from_bytes(cls, data: bytes) -> ExportIndex:
        try:
            root = json_object(json.loads(data), "index")
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise ValueError(f"invalid export index: {error}") from error
        _exact(root, {"schema", "notebook", "plan_sha256", "producer", "scenarios"}, "index")
        if root.get("schema") != INDEX_SCHEMA:
            raise ValueError(f"index.schema must be {INDEX_SCHEMA!r}")
        notebook = json_object(root.get("notebook"), "index.notebook")
        _exact(notebook, {"name", "source_sha256"}, "index.notebook")
        producer = json_object(root.get("producer"), "index.producer")
        _exact(
            producer,
            {"marimo_version", "marimo_export_version"},
            "index.producer",
        )
        raw_scenarios = root.get("scenarios")
        if not isinstance(raw_scenarios, list) or not raw_scenarios:
            raise TypeError("index.scenarios must be a non-empty array")
        scenarios = tuple(ScenarioIndex.from_wire(item) for item in raw_scenarios)
        ids = [item.id for item in scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("index.scenarios ids must be unique")
        vectors = [json_identity(item.inputs) for item in scenarios]
        if len(vectors) != len(set(vectors)):
            raise ValueError("index.scenarios must contain unique input vectors")
        return cls(
            notebook_name=_string(notebook.get("name"), "index.notebook.name"),
            notebook_source_sha256=_digest(
                notebook.get("source_sha256"), "index.notebook.source_sha256"
            ),
            plan_sha256=_digest(root.get("plan_sha256"), "index.plan_sha256"),
            producer=ProducerInfo(
                marimo_version=_string(
                    producer.get("marimo_version"), "index.producer.marimo_version"
                ),
                marimo_export_version=_string(
                    producer.get("marimo_export_version"),
                    "index.producer.marimo_export_version",
                ),
            ),
            scenarios=scenarios,
        )

    def payloads(self) -> tuple[PayloadRef, ...]:
        payloads: dict[str, PayloadRef] = {}
        for scenario in self.scenarios:
            for formats in scenario.outputs.values():
                for entry in formats.values():
                    existing = payloads.setdefault(entry.payload.key, entry.payload)
                    if existing != entry.payload:
                        raise ValueError(f"conflicting payload reference: {entry.payload.key}")
        return tuple(payloads[key] for key in sorted(payloads))


@dataclass(frozen=True)
class ExportRef:
    key: str
    sha256: str
    size: int

    def wire(self) -> JsonObject:
        return {"key": self.key, "sha256": self.sha256, "size": self.size}

    @classmethod
    def from_wire(cls, value: object) -> ExportRef:
        item = json_object(value, "ref")
        _exact(item, {"key", "sha256", "size"}, "ref")
        digest = _digest(item.get("sha256"), "ref.sha256")
        key = _string(item.get("key"), "ref.key")
        expected = f"marimo-export/indexes/{digest}.json"
        if key != expected:
            raise ValueError(f"ref.key must be {expected!r}")
        size = _positive_integer(item.get("size"), "ref.size")
        return cls(key=key, sha256=digest, size=size)


@dataclass(frozen=True)
class BuildReceipt:
    elapsed_ms: float
    scenario_count: int
    projection_count: int

    def wire(self) -> JsonObject:
        return {
            "elapsed_ms": self.elapsed_ms,
            "scenario_count": self.scenario_count,
            "projection_count": self.projection_count,
        }


def export_ref(index: ExportIndex) -> tuple[ExportRef, bytes]:
    data = index.to_bytes()
    digest = sha256_bytes(data)
    return (
        ExportRef(
            key=f"marimo-export/indexes/{digest}.json",
            sha256=digest,
            size=len(data),
        ),
        data,
    )


def _exact(value: JsonObject, expected: set[str], path: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{path} must contain exactly: {', '.join(sorted(expected))}")


def _string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{path} must be a non-empty string")
    return value


def _digest(value: object, path: str) -> str:
    digest = _string(value, path)
    if _SHA256.fullmatch(digest) is None:
        raise TypeError(f"{path} must be a lowercase SHA-256 digest")
    return digest


def _non_negative_integer(value: object, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise TypeError(f"{path} must be a non-negative integer")
    return value


def _positive_integer(value: object, path: str) -> int:
    result = _non_negative_integer(value, path)
    if result == 0:
        raise TypeError(f"{path} must be a positive integer")
    return result
