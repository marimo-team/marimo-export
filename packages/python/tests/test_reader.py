from __future__ import annotations

import math
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import msgspec
import pytest
from marimo_export import open_export
from marimo_export._json import canonical_bytes, sha256_bytes
from marimo_export._marimo.blob import to_native_blob_asset
from marimo_export.descriptors import (
    ArrowDescriptor,
    AssetRef,
    BlobAssetDescriptor,
    JsonDescriptor,
    MarimoOutputDescriptor,
    NumpyDescriptor,
    Provenance,
    ScalarDescriptor,
    asset_path,
)
from marimo_export.errors import (
    ExportUnavailableError,
    IntegrityError,
    NotebookExportError,
)
from marimo_export.exporters._runtime import blob as blob_runtime
from marimo_export.index import (
    ControlBinding,
    ExportIndex,
    NotebookProvenance,
    ProducerProvenance,
    StateEntry,
)
from marimo_export.outputs import BlobAsset
from marimo_export.wire import state_fingerprint


def _provenance(name: str) -> Provenance:
    return Provenance(python_type=f"example.{name}")


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
        to_native_blob_asset(
            blob_runtime.json(
                {"mark": "line"},
                media_type="application/vnd.vegalite.v6+json",
                filename="chart.json",
                metadata={"schema_major": 6},
            )
        )
    )
    assets = {"array": npy, "table": arrow, "chart": blob}
    descriptors = {
        "count": ScalarDescriptor(value=3, provenance=_provenance("count")),
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
    baseline_inputs = {
        "chart_width": 800,
        "symbols_selector": ["AAPL", "CRWV"],
    }
    msft_inputs = {"chart_width": 800, "symbols_selector": ["MSFT"]}
    baseline_fingerprint = state_fingerprint(baseline_inputs)
    msft_fingerprint = state_fingerprint(msft_inputs)
    index = ExportIndex(
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
        outputs=("count", "array", "table", "chart"),
        aliases={
            "baseline": baseline_fingerprint,
            "leaders": baseline_fingerprint,
            "msft": msft_fingerprint,
        },
        states={
            baseline_fingerprint: StateEntry(
                inputs=baseline_inputs,
                outputs=descriptors,
            ),
            msft_fingerprint: StateEntry(
                inputs=msft_inputs,
                outputs={
                    **descriptors,
                    "count": ScalarDescriptor(
                        value=1,
                        provenance=_provenance("count-msft"),
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
    assert export.identity == sha256_bytes(index.to_bytes())
    assert export.input_names == ("chart_width", "symbols_selector")
    assert export.control_bindings == {
        "cell-selector-0": ControlBinding(input="symbols_selector", path=())
    }
    with pytest.raises(TypeError):
        cast(dict[str, ControlBinding], export.control_bindings)["other"] = ControlBinding(
            input="symbols_selector",
            path=(),
        )
    assert export.output_names == ("count", "array", "table", "chart")
    assert export.notebook == index.notebook
    assert export.producer == index.producer
    assert tuple(state.fingerprint for state in export.states()) == tuple(sorted(index.states))
    assert baseline.aliases == ("baseline", "leaders")
    assert export.state("leaders") is baseline
    assert export.state_by_fingerprint(baseline.fingerprint) is baseline
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
        cast(Any, baseline).aliases = ("changed",)
    with pytest.raises(AttributeError, match="immutable"):
        cast(Any, baseline.output("count")).name = "changed"
    with pytest.raises(NotebookExportError) as raised:
        baseline.resolve({"missing": 1})
    assert raised.value.code == "state_input_invalid"
    with pytest.raises(NotebookExportError) as raised:
        export.resolve({"chart_width": 480, "symbols_selector": ["MSFT"]})
    assert raised.value.code == "state_unavailable"


def test_public_reader_objects_reject_slot_deletion_and_reset(tmp_path: Path) -> None:
    root, _, _ = _export(tmp_path / "export")
    export = open_export(root)
    state = export.state("baseline")
    output = state.output("count")

    for value, attribute, replacement in (
        (export, "_path", tmp_path),
        (state, "aliases", ("forged",)),
        (output, "name", "forged"),
    ):
        original = getattr(value, attribute)
        with pytest.raises(AttributeError, match=f"{type(value).__name__} is immutable"):
            delattr(value, attribute)
        with pytest.raises(AttributeError, match=f"{type(value).__name__} is immutable"):
            setattr(value, attribute, replacement)
        assert getattr(value, attribute) == original


def test_reader_json_values_and_descriptors_are_recursively_immutable(tmp_path: Path) -> None:
    fingerprint = state_fingerprint({})
    index = ExportIndex(
        spec_sha256="d" * 64,
        default_state=fingerprint,
        notebook=NotebookProvenance(filename=None, document_sha256="a" * 64),
        producer=ProducerProvenance(
            marimo="0.23.15",
            marimo_export="1.0.0",
            implementation_sha256="c" * 64,
        ),
        inputs=(),
        control_bindings={},
        outputs=("value",),
        aliases={"state": fingerprint},
        states={
            fingerprint: StateEntry(
                inputs={},
                outputs={
                    "value": JsonDescriptor(
                        value={"nested": {"count": 1}, "items": ["one"]},
                        provenance=_provenance("json"),
                    )
                },
            )
        },
    )
    root = tmp_path / "export"
    root.mkdir()
    (root / "index.json").write_bytes(index.to_bytes())

    output = open_export(root).state("state").output("value")
    with pytest.raises(TypeError):
        cast(Any, output.json())["nested"]["count"] = 2
    with pytest.raises(TypeError):
        cast(Any, cast(JsonDescriptor, output.descriptor).value)["items"][0] = "changed"

    value = cast(Mapping[str, Any], output.json())
    assert value["nested"] == {"count": 1}
    assert cast(JsonDescriptor, output.descriptor).to_value()["value"] == {
        "items": ["one"],
        "nested": {"count": 1},
    }


def test_named_lookup_errors_are_typed(tmp_path: Path) -> None:
    root, _, _ = _export(tmp_path / "export")
    export = open_export(root)

    with pytest.raises(NotebookExportError) as raised:
        export.state("missing")
    assert raised.value.code == "state_not_found"

    with pytest.raises(NotebookExportError) as raised:
        export.state_by_fingerprint("f" * 64)
    assert raised.value.code == "state_not_found"

    with pytest.raises(TypeError, match="lowercase SHA-256"):
        export.state_by_fingerprint("baseline")

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
    descriptor = cast(
        NumpyDescriptor,
        index.states[index.aliases["baseline"]].outputs["array"],
    )
    path = root / asset_path(descriptor.codec, descriptor.asset.sha256)
    data = path.read_bytes()
    path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))

    with pytest.raises(IntegrityError, match="SHA-256"):
        open_export(root).state("baseline").output("array").asset_bytes()


def test_asset_size_is_verified_before_digest(tmp_path: Path) -> None:
    root, index, _ = _export(tmp_path / "export")
    descriptor = cast(
        NumpyDescriptor,
        index.states[index.aliases["baseline"]].outputs["array"],
    )
    path = root / asset_path(descriptor.codec, descriptor.asset.sha256)
    path.write_bytes(path.read_bytes()[:-1])

    with pytest.raises(IntegrityError, match="size"):
        open_export(root).state("baseline").output("array").asset_bytes()


def test_blob_envelope_must_match_its_descriptor(tmp_path: Path) -> None:
    root, index, _ = _export(tmp_path / "export")
    descriptor = cast(
        BlobAssetDescriptor,
        index.states[index.aliases["baseline"]].outputs["chart"],
    )
    path = root / asset_path(descriptor.codec, descriptor.asset.sha256)
    changed = msgspec.msgpack.encode(
        to_native_blob_asset(
            BlobAsset(
                data=b'{"mark":"line"}',
                media_type="image/png",
                filename="chart.json",
                metadata={"schema_major": 6},
            )
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
        changed_index.states[changed_index.aliases["baseline"]].outputs["chart"],
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


def test_verify_rejects_an_incomplete_marimo_output_snapshot(tmp_path: Path) -> None:
    snapshot = canonical_bytes(
        {
            "schema": "marimo.output.v1",
            "projectionSha256": "b" * 64,
            "ownerCellId": "cell-summary",
            "resources": {
                "functions": {},
                "modelNotifications": [],
                "uiValues": {},
            },
        }
    )
    descriptor = MarimoOutputDescriptor(
        asset=AssetRef(sha256_bytes(snapshot), len(snapshot)),
        provenance=_provenance("snapshot"),
    )
    fingerprint = state_fingerprint({})
    index = ExportIndex(
        spec_sha256="d" * 64,
        default_state=fingerprint,
        notebook=NotebookProvenance(filename="notebook.py", document_sha256="a" * 64),
        producer=ProducerProvenance(
            marimo="0.24.0",
            marimo_export="0.0.0",
            implementation_sha256="c" * 64,
        ),
        inputs=(),
        control_bindings={},
        outputs=("view",),
        aliases={"baseline": fingerprint},
        states={
            fingerprint: StateEntry(inputs={}, outputs={"view": descriptor}),
        },
    )
    root = tmp_path / "export"
    root.mkdir()
    asset = root / asset_path(descriptor.codec, descriptor.asset.sha256)
    asset.parent.mkdir()
    asset.write_bytes(snapshot)
    (root / "index.json").write_bytes(index.to_bytes())

    with pytest.raises(IntegrityError) as raised:
        open_export(root).verify()

    assert raised.value.code == "asset_invalid"


def test_verify_reports_transient_asset_entry_inspection_as_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _index, _assets = _export(tmp_path / "export")
    entry = next((root / "assets").iterdir())
    native_lstat = Path.lstat

    def unavailable(path: Path, *args: object, **kwargs: object):
        if path == entry:
            raise PermissionError("asset unavailable")
        return native_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", unavailable)
    with pytest.raises(ExportUnavailableError):
        open_export(root).verify()


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX symlinks")
def test_reader_rejects_symlinked_roots_and_assets(tmp_path: Path) -> None:
    root, index, _ = _export(tmp_path / "export")
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(root, target_is_directory=True)

    with pytest.raises(NotebookExportError, match="real directory"):
        open_export(linked_root)

    descriptor = cast(
        NumpyDescriptor,
        index.states[index.aliases["baseline"]].outputs["array"],
    )
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
    fingerprint = state_fingerprint({})
    values = {
        "big": 2**63,
        "nan": math.nan,
        "infinity": math.inf,
        "negative_zero": -0.0,
    }
    for name, value in values.items():
        index = ExportIndex(
            spec_sha256="d" * 64,
            default_state=fingerprint,
            notebook=NotebookProvenance(filename=None, document_sha256="a" * 64),
            producer=ProducerProvenance(
                marimo="0.23.15",
                marimo_export="1.0.0",
                implementation_sha256="c" * 64,
            ),
            inputs=(),
            control_bindings={},
            outputs=("value",),
            aliases={"state": fingerprint},
            states={
                fingerprint: StateEntry(
                    inputs={},
                    outputs={
                        "value": ScalarDescriptor(
                            value=value,
                            provenance=_provenance(name),
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
