from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any, cast

import msgspec
import pytest
from marimo._save.stubs import BlobAsset
from marimo_export import open_export
from marimo_export._json import sha256_bytes
from marimo_export.errors import IntegrityError, NotebookExportError
from marimo_export.export import (
    ArrowDescriptor,
    AssetRef,
    BlobAssetDescriptor,
    ExportIndex,
    NotebookProvenance,
    NumpyDescriptor,
    ProducerProvenance,
    Provenance,
    ScalarDescriptor,
    StateEntry,
    asset_path,
)


def _provenance(name: str, *, asset: bool = True) -> Provenance:
    return Provenance(
        cache_key=f"cell_cache/O_{name}.json",
        return_reference=f"cell_cache/{name}/return.bin" if asset else None,
        python_type=f"example.{name}",
    )


def _npy(values: tuple[int, ...]) -> bytes:
    import struct

    header = repr(
        {
            "descr": "<i4",
            "fortran_order": False,
            "shape": (len(values),),
        }
    )
    prefix = b"\x93NUMPY\x01\x00"
    padding = (64 - ((len(prefix) + 2 + len(header) + 1) % 64)) % 64
    header_bytes = (header + " " * padding + "\n").encode("latin1")
    return (
        prefix
        + len(header_bytes).to_bytes(2, "little")
        + header_bytes
        + struct.pack(f"<{len(values)}i", *values)
    )


def _arrow() -> bytes:
    footer = b"valid-flatbuffer-placeholder"
    return b"ARROW1" + b"\x00\x00" + footer + len(footer).to_bytes(4, "little") + b"ARROW1"


def _export(root: Path) -> tuple[Path, ExportIndex, dict[str, bytes]]:
    npy = _npy((1, 2, 3))
    arrow = _arrow()
    blob = msgspec.msgpack.encode(
        BlobAsset(
            data=b'{"mark":"line"}',
            media_type="application/vnd.vegalite.v6+json",
            filename="chart.json",
            metadata={"schema_major": 6},
        )
    )
    assets = {"array": npy, "table": arrow, "chart": blob}
    descriptors = {
        "count": ScalarDescriptor(value=3, provenance=_provenance("count", asset=False)),
        "array": NumpyDescriptor(
            asset=AssetRef(sha256_bytes(npy), len(npy)),
            provenance=_provenance("array"),
        ),
        "table": ArrowDescriptor(
            asset=AssetRef(sha256_bytes(arrow), len(arrow)),
            provenance=_provenance("table"),
        ),
        "chart": BlobAssetDescriptor(
            asset=AssetRef(sha256_bytes(blob), len(blob)),
            provenance=_provenance("chart"),
            media_type="application/vnd.vegalite.v6+json",
            filename="chart.json",
            metadata={"schema_major": 6},
        ),
    }
    index = ExportIndex(
        notebook=NotebookProvenance(filename="finance.py", document_sha256="a" * 64),
        producer=ProducerProvenance(marimo="0.23.15", marimo_export="1.0.0"),
        inputs=("chart_width", "symbols_selector"),
        outputs=("count", "array", "table", "chart"),
        states={
            "baseline": StateEntry(
                inputs={
                    "chart_width": 800,
                    "symbols_selector": ["AAPL", "CRWV"],
                },
                outputs=descriptors,
            ),
            "msft": StateEntry(
                inputs={"chart_width": 800, "symbols_selector": ["MSFT"]},
                outputs={
                    **descriptors,
                    "count": ScalarDescriptor(
                        value=1,
                        provenance=_provenance("count-msft", asset=False),
                    ),
                },
            ),
        },
    )
    root.mkdir()
    (root / "assets").mkdir()
    for descriptor in descriptors.values():
        if isinstance(descriptor, ScalarDescriptor):
            continue
        name = next(
            key for key, value in assets.items() if sha256_bytes(value) == descriptor.asset.sha256
        )
        path = root / asset_path(descriptor.codec, descriptor.asset.sha256)
        path.write_bytes(assets[name])
    (root / "index.json").write_bytes(index.to_bytes())
    return root, index, assets


def test_reader_opens_resolves_and_reads_all_codec_payloads(tmp_path: Path) -> None:
    root, index, assets = _export(tmp_path / "export")

    export = open_export(root)
    baseline = export.state("baseline")

    assert export.path == root.absolute()
    assert export.input_names == ("chart_width", "symbols_selector")
    assert export.output_names == ("count", "array", "table", "chart")
    assert export.notebook == index.notebook
    assert export.producer == index.producer
    assert tuple(state.name for state in export.states()) == ("baseline", "msft")
    assert baseline.inputs == {
        "chart_width": 800,
        "symbols_selector": ("AAPL", "CRWV"),
    }
    assert baseline.output("count").scalar() == 3
    assert baseline.output("array").asset_bytes() == assets["array"]
    assert baseline.output("table").asset_bytes() == assets["table"]
    assert baseline.output("chart").asset_bytes() == assets["chart"]

    blob = baseline.output("chart").blob_asset()
    assert type(blob) is BlobAsset
    assert blob.data == b'{"mark":"line"}'
    assert blob.media_type == "application/vnd.vegalite.v6+json"
    assert blob.filename == "chart.json"
    assert blob.metadata == {"schema_major": 6}

    verification = export.verify()
    assert verification.states == 2
    assert verification.outputs == 8
    assert verification.assets == 3
    assert verification.bytes_verified == sum(map(len, assets.values()))


def test_state_resolution_is_exact_and_immutable(tmp_path: Path) -> None:
    root, _, _ = _export(tmp_path / "export")
    export = open_export(root)
    baseline = export.state("baseline")

    assert baseline.resolve({}) is baseline
    assert baseline.resolve({"symbols_selector": ["MSFT"]}) is export.state("msft")
    assert export.resolve({"chart_width": 800, "symbols_selector": ["MSFT"]}) is export.state(
        "msft"
    )

    with pytest.raises(TypeError):
        cast(Any, baseline.inputs)["chart_width"] = 480
    with pytest.raises(AttributeError, match="immutable"):
        cast(Any, export).path = tmp_path
    with pytest.raises(AttributeError, match="immutable"):
        cast(Any, baseline).name = "changed"
    with pytest.raises(AttributeError, match="immutable"):
        cast(Any, baseline.output("count")).name = "changed"
    with pytest.raises(NotebookExportError) as raised:
        baseline.resolve({"missing": 1})
    assert raised.value.code == "state_input_invalid"
    with pytest.raises(NotebookExportError) as raised:
        export.resolve({"chart_width": 480, "symbols_selector": ["MSFT"]})
    assert raised.value.code == "state_unavailable"


def test_named_lookup_errors_are_typed(tmp_path: Path) -> None:
    root, _, _ = _export(tmp_path / "export")
    export = open_export(root)

    with pytest.raises(NotebookExportError) as raised:
        export.state("missing")
    assert raised.value.code == "state_not_found"

    with pytest.raises(NotebookExportError) as raised:
        export.state("baseline").output("missing")
    assert raised.value.code == "output_not_found"


def test_codec_specific_read_methods_reject_other_codecs(tmp_path: Path) -> None:
    root, _, _ = _export(tmp_path / "export")
    state = open_export(root).state("baseline")

    with pytest.raises(NotebookExportError, match="no asset"):
        state.output("count").asset_bytes()
    with pytest.raises(NotebookExportError, match="scalar codec"):
        state.output("array").scalar()
    with pytest.raises(NotebookExportError, match="BlobAsset"):
        state.output("table").blob_asset()


def test_open_reads_only_the_index(tmp_path: Path) -> None:
    root, _, _ = _export(tmp_path / "export")
    for path in (root / "assets").iterdir():
        path.unlink()

    export = open_export(root)

    assert export.state("baseline").output("count").scalar() == 3
    with pytest.raises(IntegrityError):
        export.state("baseline").output("array").asset_bytes()


def test_asset_digest_is_verified_before_codec_decode(tmp_path: Path) -> None:
    root, index, _ = _export(tmp_path / "export")
    descriptor = cast(NumpyDescriptor, index.states["baseline"].outputs["array"])
    path = root / asset_path(descriptor.codec, descriptor.asset.sha256)
    data = path.read_bytes()
    path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))

    with pytest.raises(IntegrityError, match="SHA-256"):
        open_export(root).state("baseline").output("array").asset_bytes()


def test_asset_size_is_verified_before_digest(tmp_path: Path) -> None:
    root, index, _ = _export(tmp_path / "export")
    descriptor = cast(NumpyDescriptor, index.states["baseline"].outputs["array"])
    path = root / asset_path(descriptor.codec, descriptor.asset.sha256)
    path.write_bytes(path.read_bytes()[:-1])

    with pytest.raises(IntegrityError, match="size"):
        open_export(root).state("baseline").output("array").asset_bytes()


def test_blob_envelope_must_match_its_descriptor(tmp_path: Path) -> None:
    root, index, _ = _export(tmp_path / "export")
    descriptor = cast(BlobAssetDescriptor, index.states["baseline"].outputs["chart"])
    path = root / asset_path(descriptor.codec, descriptor.asset.sha256)
    changed = msgspec.msgpack.encode(
        BlobAsset(
            data=b'{"mark":"line"}',
            media_type="image/png",
            filename="chart.json",
            metadata={"schema_major": 6},
        )
    )
    path.write_bytes(changed)

    wire = index.to_value()
    for state in cast(dict[str, Any], wire["states"]).values():
        entry = state["outputs"]["chart"]
        entry["asset"] = {"sha256": sha256_bytes(changed), "size": len(changed)}
    changed_index = ExportIndex.from_value(wire)
    old_path = path
    new_descriptor = cast(
        BlobAssetDescriptor,
        changed_index.states["baseline"].outputs["chart"],
    )
    new_path = root / asset_path(new_descriptor.codec, new_descriptor.asset.sha256)
    old_path.rename(new_path)
    (root / "index.json").write_bytes(changed_index.to_bytes())

    with pytest.raises(IntegrityError, match="media type"):
        open_export(root).state("baseline").output("chart").blob_asset()


def test_verify_rejects_undeclared_assets(tmp_path: Path) -> None:
    root, _, _ = _export(tmp_path / "export")
    (root / "assets" / f"{'f' * 64}.bin").write_bytes(b"undeclared")

    with pytest.raises(NotebookExportError) as raised:
        open_export(root).verify()

    assert raised.value.code == "asset_undeclared"


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX symlinks")
def test_reader_rejects_symlinked_roots_and_assets(tmp_path: Path) -> None:
    root, index, _ = _export(tmp_path / "export")
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(root, target_is_directory=True)

    with pytest.raises(NotebookExportError, match="real directory"):
        open_export(linked_root)

    descriptor = cast(NumpyDescriptor, index.states["baseline"].outputs["array"])
    path = root / asset_path(descriptor.codec, descriptor.asset.sha256)
    target = tmp_path / "array.npy"
    path.rename(target)
    path.symlink_to(target)

    with pytest.raises(IntegrityError):
        open_export(root).state("baseline").output("array").asset_bytes()


def test_open_rejects_noncanonical_index_bytes(tmp_path: Path) -> None:
    root, _, _ = _export(tmp_path / "export")
    (root / "index.json").write_bytes((root / "index.json").read_bytes() + b"\n")

    with pytest.raises(NotebookExportError) as raised:
        open_export(root)

    assert raised.value.code == "export_noncanonical"


def test_special_scalars_round_trip_through_reader(tmp_path: Path) -> None:
    values = {
        "big": 2**63,
        "nan": math.nan,
        "infinity": math.inf,
        "negative_zero": -0.0,
    }
    for name, value in values.items():
        index = ExportIndex(
            notebook=NotebookProvenance(filename=None, document_sha256="a" * 64),
            producer=ProducerProvenance(marimo="0.23.15", marimo_export="1.0.0"),
            inputs=(),
            outputs=("value",),
            states={
                "state": StateEntry(
                    inputs={},
                    outputs={
                        "value": ScalarDescriptor(
                            value=value,
                            provenance=_provenance(name, asset=False),
                        )
                    },
                )
            },
        )
        root = tmp_path / name
        root.mkdir()
        (root / "index.json").write_bytes(index.to_bytes())
        loaded = open_export(root).state("state").output("value").scalar()
        if name == "nan":
            assert math.isnan(cast(float, loaded))
        elif name == "negative_zero":
            assert math.copysign(1, cast(float, loaded)) == -1
        else:
            assert loaded == value
