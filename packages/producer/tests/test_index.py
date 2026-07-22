from __future__ import annotations

import json

import pytest
from marimo_export._json import JsonObject, sha256_bytes
from marimo_export.index import (
    INDEX_SCHEMA,
    ExportIndex,
    ExportRef,
    PayloadRef,
    ProducerInfo,
    ProjectionEntry,
    ScenarioIndex,
    export_ref,
)


def make_index(payload: bytes = b"payload") -> ExportIndex:
    digest = sha256_bytes(payload)
    inputs: JsonObject = {"symbol": "MSFT"}
    entry = ProjectionEntry(
        format_id="json.v1",
        media_type="application/json",
        metadata={"rows": 1},
        payload=PayloadRef(
            key=f"marimo-export/payloads/sha256/{digest}",
            sha256=digest,
            size=len(payload),
        ),
    )
    return ExportIndex(
        notebook_name="finance.py",
        notebook_source_sha256="a" * 64,
        plan_sha256="b" * 64,
        producer=ProducerInfo("0.23.14", "0.0.0"),
        scenarios=(
            ScenarioIndex(
                id="microsoft",
                inputs=inputs,
                outputs={"summary": {"json": entry}},
            ),
        ),
    )


def test_export_index_round_trips_and_derives_payload_closure() -> None:
    index = make_index()
    parsed = ExportIndex.from_bytes(index.to_bytes())

    assert parsed == index
    assert parsed.wire()["schema"] == INDEX_SCHEMA
    assert parsed.payloads() == (index.scenarios[0].outputs["summary"]["json"].payload,)


def test_export_ref_anchors_exact_index_bytes() -> None:
    ref, data = export_ref(make_index())

    assert ref.sha256 == sha256_bytes(data)
    assert ref.key == f"marimo-export/indexes/{ref.sha256}.json"
    assert ExportRef.from_wire(ref.wire()) == ref


def test_payload_ref_rejects_non_content_addressed_key() -> None:
    digest = "a" * 64
    with pytest.raises(ValueError, match=r"payload\.key"):
        PayloadRef.from_wire({"key": "arbitrary/path", "sha256": digest, "size": 1})


def test_index_rejects_duplicate_structural_input_vectors() -> None:
    value = json.loads(make_index().to_bytes())
    duplicate = dict(value["scenarios"][0])
    duplicate["id"] = "duplicate"
    duplicate["inputs"] = {"symbol": "MSFT"}
    value["scenarios"].append(duplicate)

    with pytest.raises(ValueError, match="unique input vectors"):
        ExportIndex.from_bytes(json.dumps(value).encode())
