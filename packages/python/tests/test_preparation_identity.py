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
