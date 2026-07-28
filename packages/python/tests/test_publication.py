from __future__ import annotations

import math
from pathlib import Path
from typing import Any, cast

import pytest
from marimo_export._json import JsonObject, canonical_bytes, sha256_bytes
from marimo_export.errors import PublicationError
from marimo_export.publication import (
    ARROW_CODEC,
    BLOB_ASSET_CODEC,
    NUMPY_CODEC,
    SCALAR_CODEC,
    SCALAR_MEDIA_TYPE,
    ArrowDescriptor,
    AssetRef,
    BlobAssetDescriptor,
    CacheSummary,
    NotebookProvenance,
    NumpyDescriptor,
    ProducerProvenance,
    Provenance,
    PublicationIndex,
    PublicationResult,
    PublicationWarning,
    ScalarDescriptor,
    StateEntry,
    asset_path,
    state_fingerprint,
)


def _provenance(
    kind: str,
    *,
    asset: bool = True,
) -> Provenance:
    return Provenance(
        cache_key=f"cell_cache/P_{kind}.json",
        return_reference=f"cell_cache/{kind}/return.bin" if asset else None,
        python_type=f"example.{kind}",
    )


def _index() -> PublicationIndex:
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
    return PublicationIndex(
        notebook=NotebookProvenance(filename="finance.py", document_sha256="a" * 64),
        producer=ProducerProvenance(marimo="0.23.15", marimo_export="1.0.0"),
        inputs=("chart_width", "symbols_selector"),
        outputs=("count", "array", "chart"),
        states={
            "baseline": StateEntry(
                inputs={
                    "chart_width": 800,
                    "symbols_selector": ["AAPL", "CRWV"],
                },
                outputs={
                    "count": ScalarDescriptor(
                        value=42,
                        provenance=_provenance("count", asset=False),
                    ),
                    "array": array,
                    "chart": chart,
                },
            ),
            "msft": StateEntry(
                inputs={
                    "chart_width": 800,
                    "symbols_selector": ["MSFT"],
                },
                outputs={
                    "count": ScalarDescriptor(
                        value=1,
                        provenance=_provenance("count-msft", asset=False),
                    ),
                    "array": array,
                    "chart": chart,
                },
            ),
        },
    )


def test_publication_index_round_trips_canonical_bytes() -> None:
    index = _index()
    encoded = index.to_bytes()
    decoded = PublicationIndex.from_bytes(encoded)

    assert decoded == index
    assert encoded == canonical_bytes(index.to_value())
    assert decoded.inputs == ("chart_width", "symbols_selector")
    assert decoded.outputs == ("count", "array", "chart")
    assert tuple(decoded.states) == ("baseline", "msft")
    assert decoded.states["baseline"].fingerprint == state_fingerprint(
        {"chart_width": 800, "symbols_selector": ["AAPL", "CRWV"]}
    )


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


@pytest.mark.parametrize(
    ("value", "wire"),
    [
        (None, None),
        (True, True),
        ("hello", "hello"),
        (42, 42),
        (3.5, 3.5),
        (
            2**63,
            {"type": "bigint", "value": "9223372036854775808"},
        ),
        (
            -0.0,
            {"type": "float", "value": "negative-zero"},
        ),
        (
            math.inf,
            {"type": "float", "value": "infinity"},
        ),
        (
            -math.inf,
            {"type": "float", "value": "-infinity"},
        ),
    ],
)
def test_scalar_descriptor_uses_closed_scalar_tags(
    value: object,
    wire: object,
) -> None:
    descriptor = ScalarDescriptor(
        value=cast(Any, value),
        provenance=_provenance("scalar", asset=False),
    )

    assert descriptor.to_value()["value"] == wire
    decoded = (
        PublicationIndex.from_value(_single_output_wire(descriptor.to_value()))
        .states["state"]
        .outputs["output"]
    )
    assert isinstance(decoded, ScalarDescriptor)
    if isinstance(value, float) and math.isinf(value):
        assert decoded.value == value
    elif isinstance(value, float) and value == 0:
        assert math.copysign(1, cast(float, decoded.value)) == -1
    else:
        assert decoded.value == value


def test_nan_uses_the_tagged_float_form() -> None:
    descriptor = ScalarDescriptor(
        value=math.nan,
        provenance=_provenance("nan", asset=False),
    )
    assert descriptor.to_value()["value"] == {"type": "float", "value": "nan"}
    loaded = cast(
        ScalarDescriptor,
        PublicationIndex.from_value(_single_output_wire(descriptor.to_value()))
        .states["state"]
        .outputs["output"],
    )
    assert math.isnan(cast(float, loaded.value))


@pytest.mark.parametrize(
    "value",
    [
        {"type": "bigint", "value": "01"},
        {"type": "bigint", "value": "-0"},
        {"type": "bigint", "value": "42"},
        {"type": "float", "value": "NaN"},
        {"type": "float", "value": "other"},
        {"type": "other", "value": "nan"},
        {"type": "float", "value": "nan", "extra": True},
    ],
)
def test_publication_rejects_invalid_scalar_tags(value: object) -> None:
    wire = _single_output_wire(
        cast(
            JsonObject,
            {
                "codec": SCALAR_CODEC,
                "media_type": SCALAR_MEDIA_TYPE,
                "provenance": _provenance("scalar", asset=False).to_value(),
                "value": value,
            },
        )
    )

    with pytest.raises(PublicationError):
        PublicationIndex.from_value(wire)


def test_asset_paths_are_derived_from_codec_and_digest() -> None:
    digest = "d" * 64
    assert asset_path(NUMPY_CODEC, digest) == f"assets/{digest}.npy"
    assert asset_path(ARROW_CODEC, digest) == f"assets/{digest}.arrow"
    assert asset_path(BLOB_ASSET_CODEC, digest) == f"assets/{digest}.bin"
    with pytest.raises(ValueError):
        asset_path(SCALAR_CODEC, digest)


def test_assets_deduplicate_by_codec_and_digest() -> None:
    index = _index()

    assert index.assets() == (
        (NUMPY_CODEC, AssetRef(sha256="b" * 64, size=144)),
        (BLOB_ASSET_CODEC, AssetRef(sha256="c" * 64, size=131)),
    )


def test_same_digest_under_different_codecs_is_a_distinct_asset() -> None:
    digest = "d" * 64
    index = PublicationIndex(
        notebook=NotebookProvenance(filename=None, document_sha256="a" * 64),
        producer=ProducerProvenance(marimo="0.23.15", marimo_export="1.0.0"),
        inputs=(),
        outputs=("array", "table"),
        states={
            "one": StateEntry(
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


def test_publication_requires_exact_state_input_and_output_sets() -> None:
    index = _index()

    with pytest.raises(ValueError, match=r"publication\.inputs"):
        PublicationIndex(
            notebook=index.notebook,
            producer=index.producer,
            inputs=("chart_width",),
            outputs=index.outputs,
            states=index.states,
        )
    with pytest.raises(ValueError, match=r"publication\.outputs"):
        PublicationIndex(
            notebook=index.notebook,
            producer=index.producer,
            inputs=index.inputs,
            outputs=("count",),
            states=index.states,
        )


def test_publication_rejects_duplicate_normalized_vectors() -> None:
    index = _index()
    duplicate = StateEntry(
        inputs=index.states["baseline"].inputs,
        outputs=index.states["baseline"].outputs,
    )

    with pytest.raises(ValueError, match="equal inputs"):
        PublicationIndex(
            notebook=index.notebook,
            producer=index.producer,
            inputs=index.inputs,
            outputs=index.outputs,
            states={"one": index.states["baseline"], "two": duplicate},
        )


def test_publication_rejects_representation_changes_across_states() -> None:
    index = _index()
    changed = StateEntry(
        inputs=index.states["msft"].inputs,
        outputs={
            **index.states["msft"].outputs,
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
        PublicationIndex(
            notebook=index.notebook,
            producer=index.producer,
            inputs=index.inputs,
            outputs=index.outputs,
            states={"baseline": index.states["baseline"], "msft": changed},
        )


def test_from_value_rejects_unknown_fields_at_every_boundary() -> None:
    wire = _index().to_value()
    cast(dict[str, Any], wire["notebook"])["path"] = "/secret/finance.py"

    with pytest.raises(PublicationError, match="does not accept"):
        PublicationIndex.from_value(wire)


def test_from_bytes_rejects_noncanonical_json() -> None:
    encoded = _index().to_bytes()

    with pytest.raises(PublicationError) as raised:
        PublicationIndex.from_bytes(encoded + b"\n")

    assert raised.value.code == "publication_noncanonical"


def test_from_bytes_rejects_wrong_key_order() -> None:
    value = _index().to_value()
    noncanonical = (
        '{"schema":"marimo-export.publication.v1","notebook":'
        + __import__("json").dumps(value["notebook"], separators=(",", ":"))
        + ',"producer":'
        + __import__("json").dumps(value["producer"], separators=(",", ":"))
        + ',"inputs":'
        + __import__("json").dumps(value["inputs"], separators=(",", ":"))
        + ',"outputs":'
        + __import__("json").dumps(value["outputs"], separators=(",", ":"))
        + ',"states":'
        + __import__("json").dumps(value["states"], separators=(",", ":"))
        + "}"
    ).encode()

    with pytest.raises(PublicationError) as raised:
        PublicationIndex.from_bytes(noncanonical)

    assert raised.value.code == "publication_noncanonical"


def test_provenance_rejects_absolute_paths_and_controls() -> None:
    for value in ("/tmp/cache.json", r"C:\cache\return.bin", "bad\nkey"):
        with pytest.raises(ValueError):
            Provenance(
                cache_key=value,
                return_reference=None,
                python_type="builtins.int",
            )


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


def test_publication_result_is_a_run_local_record(tmp_path: Path) -> None:
    result = PublicationResult(
        path=(tmp_path / "publication").absolute(),
        mode="capture",
        session_id="session-1",
        notebook_filename="finance.py",
        document_sha256="a" * 64,
        producer=ProducerProvenance(marimo="0.23.15", marimo_export="1.0.0"),
        states=("baseline", "msft"),
        outputs=("chart",),
        assets=1,
        asset_bytes=100,
        index_bytes=500,
        cache=CacheSummary(hits=1, misses=1),
        warnings=(
            PublicationWarning(
                code="retired_destination_cleanup_failed",
                message="Previous directory remains.",
                details={"path": "/tmp/retired"},
            ),
        ),
    )

    assert result.to_dict()["cache"] == {"hits": 1, "misses": 1}
    assert result.to_dict()["warnings"] == [
        {
            "code": "retired_destination_cleanup_failed",
            "message": "Previous directory remains.",
            "details": {"path": "/tmp/retired"},
        }
    ]


def _single_output_wire(descriptor: JsonObject) -> JsonObject:
    inputs: JsonObject = {}
    return {
        "schema": "marimo-export.publication.v1",
        "notebook": {"filename": None, "document_sha256": "a" * 64},
        "producer": {"marimo": "0.23.15", "marimo_export": "1.0.0"},
        "inputs": [],
        "outputs": ["output"],
        "states": {
            "state": {
                "fingerprint": sha256_bytes(canonical_bytes(inputs)),
                "inputs": inputs,
                "outputs": {"output": descriptor},
            }
        },
    }
