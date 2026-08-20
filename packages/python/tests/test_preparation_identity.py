from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from marimo_export._repository.preparation import (
    PreparedExportArtifact,
)
from marimo_export._services import identity as identity_service
from marimo_export._services.plan_export import (
    artifact_matches_plan,
)
from marimo_export._services.plan_wire import decode_plan_wire, producer_from_plan_wire
from marimo_export.errors import ExecutionError
from preparation_test_support import (
    _digest,
    _identity_notebook,
    _plan_wire,
    _producer,
    _spec,
)


def test_plan_wire_rejects_local_contract_drift(tmp_path: Path) -> None:
    spec = _spec()
    producer = _producer(tmp_path / "notebook.py")
    value = _plan_wire(spec, producer)
    value["spec_sha256"] = "f" * 64

    with pytest.raises(ExecutionError, match="another export specification"):
        decode_plan_wire(value, spec, producer)


def test_artifact_plan_check_propagates_transient_storage_failure(tmp_path: Path) -> None:
    class UnavailableArtifact:
        def asset(self, relative: str):
            del relative
            raise PermissionError("temporarily unavailable")

    spec = _spec(states={"baseline": {"choice": "A"}})
    producer = _producer(tmp_path / "notebook.py")
    plan = decode_plan_wire(_plan_wire(spec, producer), spec, producer)

    with pytest.raises(PermissionError, match="temporarily unavailable"):
        artifact_matches_plan(cast(PreparedExportArtifact, UnavailableArtifact()), plan)


def test_runtime_identity_rejects_source_and_document_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    producer = _producer(tmp_path / "notebook.py")
    monkeypatch.setattr(identity_service, "producer_identity", lambda _source: producer)

    with pytest.raises(ExecutionError, match="does not match"):
        identity_service.runtime_producer_identity(
            document_sha256=_digest("different-document"),
            source=tmp_path / "notebook.py",
        )


def test_kernel_plan_preserves_pathless_producer_facts_and_rejects_bad_digests(
    tmp_path: Path,
) -> None:
    spec = _spec()
    producer = _producer(tmp_path / "notebook.py")
    wire = _plan_wire(spec, producer)

    remote = producer_from_plan_wire(wire)

    assert remote.source is None
    assert remote.filename == "notebook.py"
    assert remote.source_sha256 == producer.source_sha256
    assert remote.environment_sha256 == producer.environment_sha256

    wire["environment_sha256"] = "invalid"
    with pytest.raises(ValueError, match="environment_sha256"):
        producer_from_plan_wire(wire)


def test_hidden_managed_snapshot_does_not_change_local_environment_identity(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("value = 1\n", encoding="utf-8")
    before = identity_service._environment_identity(tmp_path)
    (tmp_path / ".module.marimo-export-snapshot.py").write_text(
        "value = 1\n",
        encoding="utf-8",
    )

    assert identity_service._environment_identity(tmp_path) == before


def test_local_environment_identity_rejects_source_change_during_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "module.py"
    source.write_text("value = 1\n", encoding="utf-8")
    native = identity_service._stable_file_sha256

    def changed(path: Path) -> str:
        digest = native(path)
        path.write_text("value = 2\n", encoding="utf-8")
        return digest

    monkeypatch.setattr(identity_service, "_stable_file_sha256", changed)

    with pytest.raises(RuntimeError, match="changed"):
        identity_service._local_source_record(tmp_path)


def test_local_source_manifest_prunes_excluded_directories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    included = tmp_path / "package"
    included.mkdir()
    (included / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    excluded = (
        tmp_path / ".venv" / "lib",
        tmp_path / ".git" / "objects",
        tmp_path / "node_modules" / "package",
        tmp_path / "site-packages" / "package",
    )
    for directory in excluded:
        directory.mkdir(parents=True)
        (directory / "ignored.py").write_text("VALUE = 1\n", encoding="utf-8")
    native_scandir = identity_service.os.scandir
    visited: list[Path] = []

    def scandir(path):
        visited.append(Path(path).resolve())
        return native_scandir(path)

    monkeypatch.setattr(identity_service.os, "scandir", scandir)

    manifest = identity_service._local_source_manifest(tmp_path)

    assert tuple(item[0] for item in manifest) == ("package/module.py",)
    assert not any(
        current == directory or current.is_relative_to(directory)
        for current in visited
        for directory in excluded
    )


def test_local_source_record_tracks_imported_roots_and_sibling_modules(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text("import package\n", encoding="utf-8")
    sibling = tmp_path / "custom_exporter.py"
    sibling.write_text("VALUE = 1\n", encoding="utf-8")
    package = tmp_path / "package"
    package.mkdir()
    (package / "__init__.py").write_text("from .module import VALUE\n", encoding="utf-8")
    module = package / "module.py"
    module.write_text("VALUE = 1\n", encoding="utf-8")
    unrelated = tmp_path / "generated" / "deep"
    unrelated.mkdir(parents=True)
    ignored = unrelated / "ignored.py"
    ignored.write_text("VALUE = 1\n", encoding="utf-8")

    before = identity_service._local_source_record(notebook)
    ignored.write_text("VALUE = 2\n", encoding="utf-8")
    after_ignored = identity_service._local_source_record(notebook)
    module.write_text("VALUE = 2\n", encoding="utf-8")
    after_import = identity_service._local_source_record(notebook)

    assert set(before) == {
        "custom_exporter.py",
        "notebook.py",
        "package/__init__.py",
        "package/module.py",
    }
    assert after_ignored == before
    assert after_import != before


def test_producer_identity_ignores_unrelated_generated_tree_and_tracks_project_package(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    _identity_notebook(notebook)
    package = tmp_path / "package"
    package.mkdir()
    module = package / "__init__.py"
    module.write_text("VALUE = 1\n", encoding="utf-8")
    generated = tmp_path / "generated" / "deep"
    generated.mkdir(parents=True)
    ignored = generated / "ignored.py"
    ignored.write_text("VALUE = 1\n", encoding="utf-8")

    before = identity_service.producer_identity(notebook)
    ignored.write_text("VALUE = 2\n", encoding="utf-8")
    after_ignored = identity_service.producer_identity(notebook)
    module.write_text("VALUE = 2\n", encoding="utf-8")
    after_package = identity_service.producer_identity(notebook)

    assert after_ignored.producer_sha256 == before.producer_sha256
    assert after_package.environment_sha256 != before.environment_sha256
    assert after_package.producer_sha256 != before.producer_sha256


def test_local_source_record_tracks_imported_editable_project_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    project = tmp_path_factory.mktemp("notebook-project")
    external = tmp_path_factory.mktemp("editable-project")
    package = external / "external_package"
    package.mkdir()
    module = package / "__init__.py"
    module.write_text("VALUE = 1\n", encoding="utf-8")
    notebook = project / "notebook.py"
    notebook.write_text("import external_package\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(external))

    before = identity_service._local_source_record(notebook)
    module.write_text("VALUE = 2\n", encoding="utf-8")
    after = identity_service._local_source_record(notebook)

    assert any(name.endswith("external_package/__init__.py") for name in before)
    assert after != before
