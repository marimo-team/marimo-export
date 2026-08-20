from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from marimo_export._json import JsonObject, canonical_bytes, sha256_bytes
from marimo_export.descriptors import (
    ARROW_CODEC,
    BLOB_ASSET_CODEC,
    JSON_CODEC,
    MARIMO_CELL_CODEC,
    MARIMO_OUTPUT_CODEC,
    NUMPY_CODEC,
    SCALAR_CODEC,
    SCALAR_MEDIA_TYPE,
    ArrowDescriptor,
    AssetRef,
    BlobAssetDescriptor,
    JsonDescriptor,
    NumpyDescriptor,
    Provenance,
    ScalarDescriptor,
    asset_path,
)
from marimo_export.errors import NotebookExportError
from marimo_export.index import (
    ControlBinding,
    ControlElementStep,
    ControlIndexStep,
    ControlKeyStep,
    ExportIndex,
    NotebookProvenance,
    ProducerProvenance,
    StateEntry,
)
from marimo_export.planning import ExportPlan, PlannedState
from marimo_export.progress import CacheActivity
from marimo_export.reader import VerificationResult
from marimo_export.result import ExportResult, ExportWarning
from marimo_export.wire import state_fingerprint


def _provenance(kind: str) -> Provenance:
    return Provenance(python_type=f"example.{kind}")


def _index() -> ExportIndex:
    array = NumpyDescriptor(
        asset=AssetRef(sha256="b" * 64, size=144),
        provenance=_provenance("array"),
    )
    chart = BlobAssetDescriptor(
        asset=AssetRef(sha256="c" * 64, size=131),
        provenance=_provenance("chart"),
        media_type="application/vnd.vegalite.v6+json",
        filename="chart.json",
        metadata={"schema_major": 6},
    )
    baseline_inputs: JsonObject = {
        "chart_width": 800,
        "symbols_selector": ["AAPL", "CRWV"],
    }
    msft_inputs: JsonObject = {
        "chart_width": 800,
        "symbols_selector": ["MSFT"],
    }
    baseline_fingerprint = state_fingerprint(baseline_inputs)
    msft_fingerprint = state_fingerprint(msft_inputs)
    return ExportIndex(
        spec_sha256="d" * 64,
        default_state=baseline_fingerprint,
        notebook=NotebookProvenance(filename="finance.py", document_sha256="a" * 64),
        producer=ProducerProvenance(
            marimo="0.23.15",
            marimo_export="1.0.0",
            implementation_sha256="c" * 64,
        ),
        inputs=("chart_width", "symbols_selector"),
        control_bindings={
            "cell-selector-0": ControlBinding(
                input="symbols_selector",
                path=(),
            )
        },
        outputs=("count", "array", "chart"),
        aliases={
            "baseline": baseline_fingerprint,
            "leaders": baseline_fingerprint,
            "msft": msft_fingerprint,
        },
        states={
            baseline_fingerprint: StateEntry(
                inputs=baseline_inputs,
                outputs={
                    "count": ScalarDescriptor(
                        value=42,
                        provenance=_provenance("count"),
                    ),
                    "array": array,
                    "chart": chart,
                },
            ),
            msft_fingerprint: StateEntry(
                inputs=msft_inputs,
                outputs={
                    "count": ScalarDescriptor(
                        value=1,
                        provenance=_provenance("count-msft"),
                    ),
                    "array": array,
                    "chart": chart,
                },
            ),
        },
    )


def test_export_index_round_trips_canonical_bytes() -> None:
    index = _index()
    encoded = index.to_bytes()
    decoded = ExportIndex.from_bytes(encoded)

    assert decoded == index
    assert encoded == canonical_bytes(index.to_value())
    assert decoded.spec_sha256 == "d" * 64
    assert decoded.default_state == decoded.aliases["baseline"]
    assert decoded.inputs == ("chart_width", "symbols_selector")
    assert decoded.outputs == ("count", "array", "chart")
    assert tuple(decoded.states) == tuple(sorted(index.states))
    assert decoded.aliases["baseline"] == state_fingerprint(
        {"chart_width": 800, "symbols_selector": ["AAPL", "CRWV"]}
    )
    assert decoded.aliases["leaders"] == decoded.aliases["baseline"]
    assert decoded.control_bindings == {
        "cell-selector-0": ControlBinding(input="symbols_selector", path=())
    }
    assert decoded.producer.implementation_sha256 == "c" * 64


def test_canonical_json_uses_ecmascript_number_spelling() -> None:
    assert canonical_bytes(
        {
            "large": 1e20,
            "small_decimal": 1e-6,
            "small_exponent": 1e-7,
            "positive_exponent": 1e21,
            "negative_zero": -0.0,
        }
    ) == (
        b'{"large":100000000000000000000,"negative_zero":0,'
        b'"positive_exponent":1e+21,"small_decimal":0.000001,"small_exponent":1e-7}'
    )


def test_scalar_descriptor_round_trips_closed_wire_tags() -> None:
    for value, wire in (
        (42, 42),
        (2**63, {"type": "bigint", "value": "9223372036854775808"}),
        (-0.0, {"type": "float", "value": "negative-zero"}),
        (math.inf, {"type": "float", "value": "infinity"}),
        (math.nan, {"type": "float", "value": "nan"}),
    ):
        descriptor = ScalarDescriptor(
            value=cast(Any, value),
            provenance=_provenance("scalar"),
        )
        assert descriptor.to_value()["value"] == wire

        index = ExportIndex.from_value(_single_output_wire(descriptor.to_value()))
        decoded = index.states[index.aliases["state"]].outputs["output"]
        assert isinstance(decoded, ScalarDescriptor)
        if isinstance(value, float) and math.isnan(value):
            assert math.isnan(cast(float, decoded.value))
        elif isinstance(value, float) and value == 0:
            assert math.copysign(1, cast(float, decoded.value)) == -1
        else:
            assert decoded.value == value


def test_json_descriptor_exposes_immutable_canonical_value() -> None:
    descriptor = JsonDescriptor(
        value={"nested": {"count": 1}, "items": ["one"]},
        provenance=_provenance("json"),
    )

    with pytest.raises(TypeError):
        cast(Any, descriptor.value)["nested"]["count"] = 2
    with pytest.raises(TypeError):
        cast(Any, descriptor.value)["items"][0] = "changed"

    detached = cast(dict[str, Any], descriptor.to_value()["value"])
    detached["nested"]["count"] = 3
    value = cast(Mapping[str, Any], descriptor.value)
    assert value["nested"] == {"count": 1}
    assert descriptor.to_value()["value"] == {
        "items": ["one"],
        "nested": {"count": 1},
    }


def test_export_rejects_invalid_scalar_tags() -> None:
    for value in (
        {"type": "bigint", "value": "01"},
        {"type": "float", "value": "other"},
        {"type": "float", "value": "nan", "extra": True},
    ):
        wire = _single_output_wire(
            cast(
                JsonObject,
                {
                    "codec": SCALAR_CODEC,
                    "media_type": SCALAR_MEDIA_TYPE,
                    "provenance": _provenance("scalar").to_value(),
                    "value": value,
                },
            )
        )
        with pytest.raises(NotebookExportError):
            ExportIndex.from_value(wire)


def test_asset_paths_are_derived_from_codec_and_digest() -> None:
    digest = "d" * 64
    assert asset_path(NUMPY_CODEC, digest) == f"assets/{digest}.npy"
    assert asset_path(ARROW_CODEC, digest) == f"assets/{digest}.arrow"
    assert asset_path(BLOB_ASSET_CODEC, digest) == f"assets/{digest}.bin"
    assert asset_path(MARIMO_OUTPUT_CODEC, digest) == f"assets/{digest}.output.json"
    assert asset_path(MARIMO_CELL_CODEC, digest) == f"assets/{digest}.cell.json"
    with pytest.raises(ValueError):
        asset_path(SCALAR_CODEC, digest)
    with pytest.raises(ValueError):
        asset_path(JSON_CODEC, digest)


def test_assets_deduplicate_by_codec_and_digest() -> None:
    index = _index()

    assert index.assets() == (
        (NUMPY_CODEC, AssetRef(sha256="b" * 64, size=144)),
        (BLOB_ASSET_CODEC, AssetRef(sha256="c" * 64, size=131)),
    )


def test_same_digest_under_different_codecs_is_a_distinct_asset() -> None:
    digest = "d" * 64
    index = ExportIndex(
        spec_sha256="e" * 64,
        default_state=state_fingerprint({}),
        notebook=NotebookProvenance(filename=None, document_sha256="a" * 64),
        producer=ProducerProvenance(
            marimo="0.23.15",
            marimo_export="1.0.0",
            implementation_sha256="c" * 64,
        ),
        inputs=(),
        control_bindings={},
        outputs=("array", "table"),
        aliases={"one": state_fingerprint({})},
        states={
            state_fingerprint({}): StateEntry(
                inputs={},
                outputs={
                    "array": NumpyDescriptor(
                        asset=AssetRef(digest, 10),
                        provenance=_provenance("array"),
                    ),
                    "table": ArrowDescriptor(
                        asset=AssetRef(digest, 10),
                        provenance=_provenance("table"),
                    ),
                },
            )
        },
    )

    assert len(index.assets()) == 2


def test_export_requires_exact_state_input_and_output_sets() -> None:
    index = _index()

    with pytest.raises(ValueError, match=r"export\.inputs"):
        ExportIndex(
            spec_sha256=index.spec_sha256,
            default_state=index.default_state,
            notebook=index.notebook,
            producer=index.producer,
            inputs=("chart_width",),
            control_bindings={},
            outputs=index.outputs,
            aliases=index.aliases,
            states=index.states,
        )
    with pytest.raises(ValueError, match=r"export\.outputs"):
        ExportIndex(
            spec_sha256=index.spec_sha256,
            default_state=index.default_state,
            notebook=index.notebook,
            producer=index.producer,
            inputs=index.inputs,
            control_bindings=index.control_bindings,
            outputs=("count",),
            aliases=index.aliases,
            states=index.states,
        )


def test_export_control_bindings_are_typed_bounded_and_name_declared_inputs() -> None:
    index = _index()

    with pytest.raises(ValueError, match="255"):
        replace(index, inputs=("x" * 256,))
    with pytest.raises(ValueError, match="declared input"):
        replace(
            index,
            control_bindings={"cell-selector-0": ControlBinding(input="missing", path=())},
        )
    with pytest.raises(ValueError, match="1024"):
        replace(
            index,
            control_bindings={"x" * 1_025: ControlBinding(input="symbols_selector", path=())},
        )
    with pytest.raises(ValueError, match="255"):
        ControlBinding(input="x" * 256, path=())

    assert ControlBinding(
        input="symbols_selector",
        path=(
            ControlIndexStep(value=0),
            ControlKeyStep(value="country"),
            ControlElementStep(),
        ),
    ).to_value() == {
        "input": "symbols_selector",
        "path": [
            {"kind": "index", "value": 0},
            {"kind": "key", "value": "country"},
            {"kind": "element"},
        ],
    }
    with pytest.raises(ValueError, match="nonnegative safe integer"):
        ControlIndexStep(value=-1)
    with pytest.raises(ValueError, match="1024"):
        ControlKeyStep(value="x" * 1_025)


def test_export_rejects_malformed_control_binding_wire_steps() -> None:
    for step in (
        {"kind": "element", "value": 0},
        {"kind": "index", "value": -1},
        {"kind": "key", "value": "x", "extra": True},
    ):
        wire = _index().to_value()
        wire["control_bindings"] = {
            "cell-selector-0": {
                "input": "symbols_selector",
                "path": [step],
            }
        }
        with pytest.raises(NotebookExportError):
            ExportIndex.from_value(wire)


def test_export_rejects_a_state_key_that_disagrees_with_its_inputs() -> None:
    index = _index()
    fingerprint = index.aliases["baseline"]

    with pytest.raises(ValueError, match="does not match state inputs"):
        ExportIndex(
            spec_sha256=index.spec_sha256,
            default_state="f" * 64,
            notebook=index.notebook,
            producer=index.producer,
            inputs=index.inputs,
            control_bindings=index.control_bindings,
            outputs=index.outputs,
            aliases={"baseline": "f" * 64},
            states={"f" * 64: index.states[fingerprint]},
        )


def test_export_rejects_representation_changes_across_states() -> None:
    index = _index()
    msft_fingerprint = index.aliases["msft"]
    changed = StateEntry(
        inputs=index.states[msft_fingerprint].inputs,
        outputs={
            **index.states[msft_fingerprint].outputs,
            "chart": BlobAssetDescriptor(
                asset=AssetRef("e" * 64, 100),
                provenance=_provenance("chart-other"),
                media_type="image/png",
                filename="chart.png",
                metadata={},
            ),
        },
    )

    with pytest.raises(ValueError, match="changes codec or media type"):
        ExportIndex(
            spec_sha256=index.spec_sha256,
            default_state=index.default_state,
            notebook=index.notebook,
            producer=index.producer,
            inputs=index.inputs,
            control_bindings=index.control_bindings,
            outputs=index.outputs,
            aliases=index.aliases,
            states={
                index.aliases["baseline"]: index.states[index.aliases["baseline"]],
                msft_fingerprint: changed,
            },
        )


def test_from_value_rejects_unknown_fields_at_every_boundary() -> None:
    wire = _index().to_value()
    cast(dict[str, Any], wire["notebook"])["path"] = "/secret/finance.py"

    with pytest.raises(NotebookExportError, match="does not accept"):
        ExportIndex.from_value(wire)


def test_from_value_requires_new_v1_root_fields() -> None:
    wire = _index().to_value()
    del wire["spec_sha256"]
    del wire["default_state"]

    with pytest.raises(NotebookExportError, match="default_state, spec_sha256"):
        ExportIndex.from_value(wire)


def test_export_requires_default_state_to_reference_a_declared_fingerprint() -> None:
    with pytest.raises(ValueError, match="default_state"):
        replace(_index(), default_state="f" * 64)
    with pytest.raises(ValueError, match="spec_sha256"):
        replace(_index(), spec_sha256="invalid")


def test_from_value_requires_exact_producer_implementation_identity() -> None:
    missing = _index().to_value()
    del cast(dict[str, Any], missing["producer"])["implementation_sha256"]
    with pytest.raises(NotebookExportError, match="implementation_sha256"):
        ExportIndex.from_value(missing)

    invalid = _index().to_value()
    cast(dict[str, Any], invalid["producer"])["implementation_sha256"] = "invalid"
    with pytest.raises(NotebookExportError, match="lowercase SHA-256"):
        ExportIndex.from_value(invalid)


def test_from_bytes_rejects_noncanonical_json() -> None:
    encoded = _index().to_bytes()

    with pytest.raises(NotebookExportError) as raised:
        ExportIndex.from_bytes(encoded + b"\n")

    assert raised.value.code == "export_noncanonical"


def test_provenance_serializes_only_the_python_type() -> None:
    provenance = Provenance(python_type="builtins.int")

    assert provenance.to_value() == {"python_type": "builtins.int"}


def test_provenance_rejects_controls() -> None:
    for value in ("bad\nname", "bad\x00name"):
        with pytest.raises(ValueError):
            Provenance(python_type=value)


def test_from_value_rejects_private_cache_receipt_fields_in_provenance() -> None:
    for field, value in (
        ("cache_key", "cell_cache/O_count.json"),
        ("return_reference", None),
    ):
        wire = _index().to_value()
        state = cast(dict[str, Any], next(iter(cast(dict[str, Any], wire["states"]).values())))
        outputs = cast(dict[str, Any], state["outputs"])
        provenance = cast(dict[str, Any], cast(dict[str, Any], outputs["count"])["provenance"])
        provenance[field] = value

        with pytest.raises(NotebookExportError, match="does not accept"):
            ExportIndex.from_value(wire)


def test_blob_metadata_is_bounded_to_256_kib() -> None:
    overhead = len(canonical_bytes({"value": ""}))
    accepted = {"value": "x" * (256 * 1024 - overhead)}
    descriptor = BlobAssetDescriptor(
        asset=AssetRef("f" * 64, 1),
        provenance=_provenance("blob"),
        media_type="application/json",
        filename=None,
        metadata=accepted,
    )
    assert descriptor.metadata == accepted

    with pytest.raises(ValueError, match="262144"):
        BlobAssetDescriptor(
            asset=AssetRef("f" * 64, 1),
            provenance=_provenance("blob"),
            media_type="application/json",
            filename=None,
            metadata={"value": accepted["value"] + "x"},
        )


def test_export_result_reports_verified_write_and_preparation_facts(tmp_path: Path) -> None:
    baseline_inputs: JsonObject = {"symbol": "AAPL"}
    msft_inputs: JsonObject = {"symbol": "MSFT"}
    baseline = state_fingerprint(baseline_inputs)
    msft = state_fingerprint(msft_inputs)
    plan = ExportPlan(
        document_sha256="a" * 64,
        producer_sha256="b" * 64,
        output_plan_sha256="c" * 64,
        spec_sha256="d" * 64,
        default_alias="baseline",
        default_fingerprint=baseline,
        inputs=("symbol",),
        states=(
            PlannedState(aliases=("baseline",), inputs=baseline_inputs, fingerprint=baseline),
            PlannedState(aliases=("msft",), inputs=msft_inputs, fingerprint=msft),
        ),
        outputs=("chart",),
        reusable_states=(baseline,),
        missing_states=(msft,),
    )
    result = ExportResult(
        path=(tmp_path / "export").absolute(),
        identity="e" * 64,
        plan=plan,
        reused=False,
        prepared_states=(msft,),
        reused_states=(baseline,),
        cache_activity=CacheActivity(
            authored_hits=3,
            authored_misses=2,
            projection_hits=1,
            projection_misses=1,
        ),
        assets=1,
        asset_bytes=100,
        index_bytes=500,
        verification=VerificationResult(states=2, outputs=2, assets=1, bytes_verified=100),
        warnings=(
            ExportWarning(
                code="retired_destination_cleanup_failed",
                message="Previous directory remains.",
                details={"path": "/tmp/retired"},
            ),
        ),
        elapsed_seconds=2.0,
    )

    assert result.prepared_states == (msft,)
    assert result.reused_states == (baseline,)
    assert result.cache_activity.projection_misses == 1
    assert result.to_dict() == {
        "path": str((tmp_path / "export").absolute()),
        "identity": "e" * 64,
        "plan": plan.to_dict(),
        "reused": False,
        "prepared_states": [msft],
        "reused_states": [baseline],
        "cache_activity": {
            "authored_hits": 3,
            "authored_misses": 2,
            "projection_hits": 1,
            "projection_misses": 1,
        },
        "assets": 1,
        "asset_bytes": 100,
        "index_bytes": 500,
        "verification": {
            "states": 2,
            "outputs": 2,
            "assets": 1,
            "bytes_verified": 100,
        },
        "warnings": [
            {
                "code": "retired_destination_cleanup_failed",
                "message": "Previous directory remains.",
                "details": {"path": "/tmp/retired"},
            }
        ],
        "elapsed_seconds": 2.0,
    }

    with pytest.raises(ValueError, match="cover the export plan"):
        replace(result, prepared_states=())


def _single_output_wire(descriptor: JsonObject) -> JsonObject:
    inputs: JsonObject = {}
    fingerprint = sha256_bytes(canonical_bytes(inputs))
    return {
        "schema": "marimo-export.export.v1",
        "spec_sha256": "d" * 64,
        "default_state": fingerprint,
        "notebook": {"filename": None, "document_sha256": "a" * 64},
        "producer": {
            "marimo": "0.23.15",
            "marimo_export": "1.0.0",
            "implementation_sha256": "c" * 64,
        },
        "inputs": [],
        "control_bindings": {},
        "outputs": ["output"],
        "aliases": {"state": fingerprint},
        "states": {
            fingerprint: {
                "inputs": inputs,
                "outputs": {"output": descriptor},
            }
        },
    }
