from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from marimo_export._repository.preparation import (
    RepositoryIdentity,
    preparation_repository,
)
from marimo_export.descriptors import Provenance, ScalarDescriptor
from marimo_export.index import (
    ExportIndex,
    NotebookProvenance,
    ProducerProvenance,
    StateEntry,
)
from marimo_export.repository import (
    ExportRepository,
)
from marimo_export.wire import state_fingerprint


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _export(
    repository: ExportRepository,
    identity: RepositoryIdentity,
    state,
    content: str,
    *,
    revision: int = 0,
    replacing: str | None = None,
):
    preparation = preparation_repository(repository)
    with (
        preparation.reserve_preparation(identity),
        preparation.stage_export(identity) as staged,
    ):
        _write_index(
            staged.path,
            identity,
            state.state_fingerprint,
            int(state.metadata["value"]),
            content,
        )
        return staged.commit(
            states=(state,),
            captured_observation_revision=revision,
            replacing_instance=replacing,
        )


def _identity(name: str = "one") -> RepositoryIdentity:
    return RepositoryIdentity(
        producer_sha256=_digest(f"producer-{name}"),
        output_plan_sha256=_digest("outputs"),
        spec_sha256=_digest(f"spec-{name}"),
    )


def _state(repository: ExportRepository, identity: RepositoryIdentity, value: int):
    fingerprint = state_fingerprint({"value": value})
    preparation = preparation_repository(repository)
    with (
        preparation.reserve_preparation(identity),
        preparation.stage_prepared_state(
            producer_sha256=identity.producer_sha256,
            output_plan_sha256=identity.output_plan_sha256,
            state_fingerprint=fingerprint,
        ) as staged,
    ):
        (staged.path / "value.txt").write_text(str(value), encoding="utf-8")
        return staged.commit(metadata={"value": value})


def _write_index(
    path: Path,
    identity: RepositoryIdentity,
    fingerprint: str,
    input_value: int,
    output_value: str,
) -> None:
    index = ExportIndex(
        spec_sha256=identity.spec_sha256,
        default_state=fingerprint,
        notebook=NotebookProvenance(
            filename="notebook.py",
            document_sha256=_digest("document"),
        ),
        producer=ProducerProvenance(
            marimo="0.24.0",
            marimo_export="0.0.0",
            implementation_sha256=_digest("implementation"),
        ),
        inputs=("value",),
        control_bindings={},
        outputs=("result",),
        aliases={"baseline": fingerprint},
        states={
            fingerprint: StateEntry(
                inputs={"value": input_value},
                outputs={
                    "result": ScalarDescriptor(
                        value=output_value,
                        provenance=Provenance(python_type="builtins.str"),
                    )
                },
            )
        },
    )
    (path / "index.json").write_bytes(index.to_bytes())


__all__ = ["_digest", "_export", "_identity", "_state", "_write_index"]
