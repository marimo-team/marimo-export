from __future__ import annotations

import os
from pathlib import Path

import marimo_export._directory as directory_module
import marimo_export._writer as writer_module
import marimo_export.index as index_module
import pytest
from marimo_export import open_export
from marimo_export._json import JsonValue, sha256_bytes
from marimo_export._writer import materialize_export, write_export
from marimo_export.descriptors import (
    AssetRef,
    NumpyDescriptor,
    OutputCodec,
    Provenance,
    ScalarDescriptor,
)
from marimo_export.errors import NotebookExportError
from marimo_export.index import ExportIndex, NotebookProvenance, ProducerProvenance, StateEntry
from marimo_export.wire import state_fingerprint


def _npy() -> bytes:
    header = repr({"descr": "|u1", "fortran_order": False, "shape": (3,)})
    prefix = b"\x93NUMPY\x01\x00"
    padding = (64 - ((len(prefix) + 2 + len(header) + 1) % 64)) % 64
    header_bytes = (header + " " * padding + "\n").encode("latin1")
    return prefix + len(header_bytes).to_bytes(2, "little") + header_bytes + b"\x01\x02\x03"


def _export() -> tuple[ExportIndex, dict[tuple[OutputCodec, str], bytes]]:
    payload = _npy()
    digest = sha256_bytes(payload)
    fingerprint = state_fingerprint({})
    index = ExportIndex(
        spec_sha256="d" * 64,
        default_state=fingerprint,
        notebook=NotebookProvenance(filename="notebook.py", document_sha256="a" * 64),
        producer=ProducerProvenance(
            marimo="0.23.15",
            marimo_export="1.0.0",
            implementation_sha256="c" * 64,
        ),
        inputs=(),
        control_bindings={},
        outputs=("count", "array"),
        aliases={"state": fingerprint},
        states={
            fingerprint: StateEntry(
                inputs={},
                outputs={
                    "count": ScalarDescriptor(
                        value=3,
                        provenance=Provenance(python_type="builtins.int"),
                    ),
                    "array": NumpyDescriptor(
                        asset=AssetRef(digest, len(payload)),
                        provenance=Provenance(python_type="numpy.ndarray"),
                    ),
                },
            )
        },
    )
    identity: tuple[OutputCodec, str] = ("numpy.npy.v1", digest)
    return index, {identity: payload}


def test_writer_stages_verifies_and_commits_a_export(tmp_path: Path) -> None:
    index, assets = _export()
    target = tmp_path / "export"

    result = write_export(index, assets, target, replace=False)

    assert result.path == target.absolute()
    assert result.assets == 1
    assert result.asset_bytes == len(next(iter(assets.values())))
    assert result.index_bytes == len(index.to_bytes())
    assert result.warnings == ()
    assert open_export(target).verify().assets == 1
    assert next(target.joinpath("assets").iterdir()).read_bytes() == next(iter(assets.values()))


def _wide_index(*, states: int, outputs: int) -> ExportIndex:
    names = tuple(f"output_{position}" for position in range(outputs))
    entries: dict[str, StateEntry] = {}
    for position in range(states):
        inputs = {"state": position}
        entries[state_fingerprint(inputs)] = StateEntry(
            inputs=inputs,
            outputs={
                name: ScalarDescriptor(
                    value=position,
                    provenance=Provenance(python_type="builtins.int"),
                )
                for name in names
            },
        )
    return ExportIndex(
        spec_sha256="d" * 64,
        default_state=state_fingerprint({"state": 0}),
        notebook=NotebookProvenance(filename="notebook.py", document_sha256="a" * 64),
        producer=ProducerProvenance(
            marimo="0.23.15",
            marimo_export="1.0.0",
            implementation_sha256="c" * 64,
        ),
        inputs=("state",),
        control_bindings={},
        outputs=names,
        aliases={},
        states=entries,
    )


def _json_values(value: JsonValue) -> int:
    if isinstance(value, dict):
        return 1 + sum(1 + _json_values(item) for item in value.values())
    if isinstance(value, list):
        return 1 + sum(_json_values(item) for item in value)
    return 1


def test_writer_and_reader_accept_an_index_of_120_states_and_100_outputs(
    tmp_path: Path,
) -> None:
    index = _wide_index(states=120, outputs=100)
    target = tmp_path / "export"
    assert _json_values(index.to_value()) > 100_000

    write_export(index, {}, target, replace=False)

    opened = open_export(target)
    assert len(opened.states()) == 120
    assert len(opened.output_names) == 100
    assert opened.resolve({"state": 119}).output("output_99").scalar() == 119


def test_writer_accepts_an_index_at_the_value_limit_and_rejects_one_more_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _wide_index(states=2, outputs=2)
    values = _json_values(index.to_value())
    monkeypatch.setattr(index_module, "_MAX_INDEX_VALUES", values)
    write_export(index, {}, tmp_path / "at-limit", replace=False)

    monkeypatch.setattr(index_module, "_MAX_INDEX_VALUES", values - 1)
    target = tmp_path / "over-limit"
    with pytest.raises(NotebookExportError) as raised:
        write_export(index, {}, target, replace=False)

    assert raised.value.code == "export_invalid"
    assert raised.value.details == {"index_values": values, "max_index_values": values - 1}
    assert not target.exists()


def test_reader_rejects_an_index_over_the_value_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _wide_index(states=2, outputs=2)
    target = tmp_path / "export"
    write_export(index, {}, target, replace=False)
    monkeypatch.setattr(
        index_module,
        "_MAX_INDEX_VALUES",
        _json_values(index.to_value()) - 1,
    )

    with pytest.raises(NotebookExportError) as raised:
        open_export(target)

    assert raised.value.code == "export_invalid"


def test_writer_materializes_into_an_owned_empty_directory(tmp_path: Path) -> None:
    index, assets = _export()
    target = tmp_path / "owned-stage"
    target.mkdir()

    result = materialize_export(index, assets, target)

    assert result.path == target
    assert result.warnings == ()
    assert open_export(target).verify().assets == 1


def test_writer_rejects_a_nonempty_materialization_directory(tmp_path: Path) -> None:
    index, assets = _export()
    target = tmp_path / "owned-stage"
    target.mkdir()
    target.joinpath("existing.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="empty directory"):
        materialize_export(index, assets, target)

    assert target.joinpath("existing.txt").read_text(encoding="utf-8") == "keep"


def test_writer_commits_through_the_shared_directory_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, assets = _export()
    target = tmp_path / "export"
    native_commit = writer_module.commit_directory
    committed: list[Path] = []

    def commit(staged, selected, *, retain_replaced=False):
        committed.append(selected.path)
        return native_commit(
            staged,
            selected,
            retain_replaced=retain_replaced,
        )

    monkeypatch.setattr(writer_module, "commit_directory", commit)

    result = write_export(index, assets, target, replace=False)

    assert committed == [target]
    assert result.path == target.absolute()
    assert open_export(target).verify().assets == 1


def test_writer_requires_explicit_replacement(tmp_path: Path) -> None:
    index, assets = _export()
    target = tmp_path / "export"
    write_export(index, assets, target, replace=False)

    with pytest.raises(NotebookExportError) as raised:
        write_export(index, assets, target, replace=False)

    assert raised.value.code == "destination_exists"


def test_writer_atomically_replaces_a_verified_export(tmp_path: Path) -> None:
    index, assets = _export()
    target = tmp_path / "export"
    write_export(index, assets, target, replace=False)
    before = (target / "index.json").read_bytes()

    result = write_export(index, assets, target, replace=True)

    assert result.warnings == ()
    assert (target / "index.json").read_bytes() == before
    assert not tuple(tmp_path.glob(".export.retired-*"))
    assert not tuple(tmp_path.glob(".export.staging-*"))


@pytest.mark.parametrize("transaction", ["exchange", "fallback"])
def test_writer_reports_retired_destination_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transaction: str,
) -> None:
    index, assets = _export()
    target = tmp_path / "export"
    write_export(index, assets, target, replace=False)
    native_remove = writer_module.shutil.rmtree

    def exchange(first: Path, second: Path) -> bool:
        if transaction == "fallback":
            return False
        temporary = tmp_path / "exchange"
        os.replace(first, temporary)
        os.replace(second, first)
        os.replace(temporary, second)
        return True

    def fail_retired(path: Path) -> None:
        if path.name.startswith((".export.staging-", ".export.recovery-")):
            raise OSError("cleanup failed")
        native_remove(path)

    monkeypatch.setattr(directory_module, "_exchange_directories", exchange)
    monkeypatch.setattr(writer_module.shutil, "rmtree", fail_retired)

    result = write_export(index, assets, target, replace=True)

    assert open_export(target).verify().assets == 1
    assert [warning.code for warning in result.warnings] == ["retired_destination_cleanup_failed"]


def test_writer_rejects_destination_root_replacement_during_commit(
    tmp_path: Path,
) -> None:
    index, assets = _export()
    target = tmp_path / "export"
    write_export(index, assets, target, replace=False)
    previous = tmp_path / "previous"

    def replace_root() -> None:
        os.replace(target, previous)
        target.mkdir()

    with pytest.raises(NotebookExportError) as raised:
        write_export(
            index,
            assets,
            target,
            replace=True,
            commit_guard=replace_root,
        )

    assert raised.value.code == "destination_changed"
    assert target.is_dir()
    assert tuple(target.iterdir()) == ()
    assert previous.joinpath("index.json").is_file()


def test_writer_commit_guard_cancellation_removes_staging(tmp_path: Path) -> None:
    index, assets = _export()
    target = tmp_path / "export"

    def cancel_commit() -> None:
        raise KeyboardInterrupt("cancelled")

    with pytest.raises(KeyboardInterrupt, match="cancelled"):
        write_export(
            index,
            assets,
            target,
            replace=False,
            commit_guard=cancel_commit,
        )

    assert not target.exists()
    assert not tuple(tmp_path.glob(".export.staging-*"))


def test_writer_reports_parent_sync_failure_after_a_visible_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, assets = _export()
    target = tmp_path / "export"
    native_sync = writer_module._sync_directory

    def fail_parent_sync(path: Path) -> None:
        if path == tmp_path:
            raise OSError("sync failed")
        native_sync(path)

    monkeypatch.setattr(writer_module, "_sync_directory", fail_parent_sync)

    result = write_export(index, assets, target, replace=False)

    assert open_export(target).verify().assets == 1
    assert [warning.code for warning in result.warnings] == ["export_parent_sync_failed"]


def test_writer_rejects_missing_extra_and_mismatched_assets(tmp_path: Path) -> None:
    index, assets = _export()
    identity, payload = next(iter(assets.items()))

    with pytest.raises(NotebookExportError) as raised:
        write_export(index, {}, tmp_path / "missing", replace=False)
    assert raised.value.code == "asset_conflict"

    with pytest.raises(NotebookExportError) as raised:
        write_export(
            index,
            {identity: payload, ("numpy.npy.v1", "f" * 64): b"extra"},
            tmp_path / "extra",
            replace=False,
        )
    assert raised.value.code == "asset_conflict"

    with pytest.raises(NotebookExportError) as raised:
        write_export(
            index,
            {identity: payload[:-1]},
            tmp_path / "changed",
            replace=False,
        )
    assert raised.value.code == "asset_conflict"


def test_writer_replaces_any_existing_real_directory(tmp_path: Path) -> None:
    index, assets = _export()
    target = tmp_path / "export"
    target.mkdir()
    (target / "unrelated.txt").write_text("user data", encoding="utf-8")

    result = write_export(index, assets, target, replace=True)

    assert result.path == target.absolute()
    assert not (target / "unrelated.txt").exists()
    assert open_export(target).verify().assets == 1


def test_writer_preflight_requires_an_existing_parent(tmp_path: Path) -> None:
    index, assets = _export()
    target = tmp_path / "missing" / "export"

    with pytest.raises(NotebookExportError) as raised:
        write_export(index, assets, target, replace=False)

    assert raised.value.code == "destination_invalid"
    assert not target.parent.exists()
