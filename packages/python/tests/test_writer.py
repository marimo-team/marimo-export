from __future__ import annotations

from pathlib import Path

import pytest
from marimo_export import PublicationError, open_publication
from marimo_export._json import sha256_bytes
from marimo_export._writer import write_publication
from marimo_export.publication import (
    AssetRef,
    NotebookProvenance,
    NumpyDescriptor,
    OutputCodec,
    ProducerProvenance,
    Provenance,
    PublicationIndex,
    ScalarDescriptor,
    StateEntry,
)


def _npy() -> bytes:
    header = repr({"descr": "|u1", "fortran_order": False, "shape": (3,)})
    prefix = b"\x93NUMPY\x01\x00"
    padding = (64 - ((len(prefix) + 2 + len(header) + 1) % 64)) % 64
    header_bytes = (header + " " * padding + "\n").encode("latin1")
    return prefix + len(header_bytes).to_bytes(2, "little") + header_bytes + b"\x01\x02\x03"


def _publication() -> tuple[PublicationIndex, dict[tuple[OutputCodec, str], bytes]]:
    payload = _npy()
    digest = sha256_bytes(payload)
    index = PublicationIndex(
        notebook=NotebookProvenance(filename="notebook.py", document_sha256="a" * 64),
        producer=ProducerProvenance(marimo="0.23.15", marimo_export="1.0.0"),
        inputs=(),
        outputs=("count", "array"),
        states={
            "state": StateEntry(
                inputs={},
                outputs={
                    "count": ScalarDescriptor(
                        value=3,
                        provenance=Provenance(
                            cache_key="cell_cache/count.json",
                            return_reference=None,
                            python_type="builtins.int",
                        ),
                    ),
                    "array": NumpyDescriptor(
                        asset=AssetRef(digest, len(payload)),
                        provenance=Provenance(
                            cache_key="cell_cache/array.json",
                            return_reference="cell_cache/array/return.npy",
                            python_type="numpy.ndarray",
                        ),
                    ),
                },
            )
        },
    )
    identity: tuple[OutputCodec, str] = ("numpy.npy.v1", digest)
    return index, {identity: payload}


def test_writer_stages_verifies_and_commits_a_publication(tmp_path: Path) -> None:
    index, assets = _publication()
    target = tmp_path / "publication"

    result = write_publication(index, assets, target, replace=False)

    assert result.path == target.absolute()
    assert result.assets == 1
    assert result.asset_bytes == len(next(iter(assets.values())))
    assert result.index_bytes == len(index.to_bytes())
    assert result.warnings == ()
    assert open_publication(target).verify().assets == 1


def test_writer_requires_explicit_replacement(tmp_path: Path) -> None:
    index, assets = _publication()
    target = tmp_path / "publication"
    write_publication(index, assets, target, replace=False)

    with pytest.raises(PublicationError) as raised:
        write_publication(index, assets, target, replace=False)

    assert raised.value.code == "destination_exists"


def test_writer_atomically_replaces_a_verified_publication(tmp_path: Path) -> None:
    index, assets = _publication()
    target = tmp_path / "publication"
    write_publication(index, assets, target, replace=False)
    before = (target / "index.json").read_bytes()

    result = write_publication(index, assets, target, replace=True)

    assert result.warnings == ()
    assert (target / "index.json").read_bytes() == before
    assert not tuple(tmp_path.glob(".publication.retired-*"))
    assert not tuple(tmp_path.glob(".publication.staging-*"))


def test_writer_rejects_missing_extra_and_mismatched_assets(tmp_path: Path) -> None:
    index, assets = _publication()
    identity, payload = next(iter(assets.items()))

    with pytest.raises(PublicationError) as raised:
        write_publication(index, {}, tmp_path / "missing", replace=False)
    assert raised.value.code == "asset_conflict"

    with pytest.raises(PublicationError) as raised:
        write_publication(
            index,
            {identity: payload, ("numpy.npy.v1", "f" * 64): b"extra"},
            tmp_path / "extra",
            replace=False,
        )
    assert raised.value.code == "asset_conflict"

    with pytest.raises(PublicationError) as raised:
        write_publication(
            index,
            {identity: payload[:-1]},
            tmp_path / "changed",
            replace=False,
        )
    assert raised.value.code == "asset_conflict"


def test_writer_replaces_any_existing_real_directory(tmp_path: Path) -> None:
    index, assets = _publication()
    target = tmp_path / "publication"
    target.mkdir()
    (target / "unrelated.txt").write_text("user data", encoding="utf-8")

    result = write_publication(index, assets, target, replace=True)

    assert result.path == target.absolute()
    assert not (target / "unrelated.txt").exists()
    assert open_publication(target).verify().assets == 1


def test_writer_preflight_requires_an_existing_parent(tmp_path: Path) -> None:
    index, assets = _publication()
    target = tmp_path / "missing" / "publication"

    with pytest.raises(PublicationError) as raised:
        write_publication(index, assets, target, replace=False)

    assert raised.value.code == "destination_invalid"
    assert not target.parent.exists()
