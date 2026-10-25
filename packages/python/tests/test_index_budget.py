from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import marimo_export._index_budget as budget_module
import marimo_export.index as index_module
import pytest
from marimo_export._index_budget import IndexProjection
from marimo_export._json import JsonObject, json_value_count
from marimo_export._services.capture_export import capture_session, plan_session
from marimo_export._services.plan_export import plan
from marimo_export._services.prepare_export import prepare
from marimo_export.descriptors import (
    AssetRef,
    JsonDescriptor,
    NumpyDescriptor,
    OutputEntry,
    Provenance,
    ScalarDescriptor,
    UnavailableOutput,
)
from marimo_export.errors import NotebookExportError
from marimo_export.index import ExportIndex, NotebookProvenance, ProducerProvenance, StateEntry
from marimo_export.progress import ProgressEvent
from marimo_export.repository import ExportRepository
from marimo_export.spec import ExportSpec, OutputSpec
from marimo_export.wire import state_fingerprint
from preparation_test_support import (
    _BorrowedSession,
    _FakeProducer,
    _identity_notebook,
    _plan_wire,
    _preflight,
    _spec,
)

_THREE_STATES: dict[str, JsonObject] = {
    "baseline": {"choice": "A"},
    "second": {"choice": "B"},
    "third": {"choice": "C"},
}


def _numbers(position: int) -> dict[str, OutputEntry]:
    return {
        "array": NumpyDescriptor(
            asset=AssetRef(sha256=str(position) * 64, size=128 + position),
            provenance=Provenance(python_type="numpy.ndarray"),
        ),
        "count": ScalarDescriptor(
            value=position,
            provenance=Provenance(python_type="builtins.int"),
        ),
    }


def _cheapest_entries(_position: int) -> dict[str, OutputEntry]:
    return {
        "größe": JsonDescriptor(value=0, provenance=Provenance(python_type="a")),
        "chart": UnavailableOutput(reason="stopped", message="a"),
    }


def _stopped_chart(_position: int) -> dict[str, OutputEntry]:
    return {"chart": UnavailableOutput(reason="stopped", message="the chart cell called mo.stop")}


def _measured_index(
    outputs: Callable[[int], dict[str, OutputEntry]] = _numbers,
) -> ExportIndex:
    states: dict[str, StateEntry] = {}
    aliases: dict[str, str] = {}
    for position, choice in enumerate(("A", "B", "C")):
        inputs: JsonObject = {"choice": choice, "width": position}
        fingerprint = state_fingerprint(inputs)
        aliases[f"state_{position}"] = fingerprint
        states[fingerprint] = StateEntry(inputs=inputs, outputs=outputs(position))
    # Single-character provenance text is what a projection assumes before
    # capture, so a fully measured projection has no slack in bytes.
    return ExportIndex(
        spec_sha256="d" * 64,
        default_state=aliases["state_0"],
        notebook=NotebookProvenance(filename="a", document_sha256="a" * 64),
        producer=ProducerProvenance(marimo="a", marimo_export="a", implementation_sha256="c" * 64),
        inputs=("choice", "width"),
        control_bindings={},
        outputs=tuple(sorted(outputs(0))),
        aliases=aliases,
        states=states,
    )


def _projection(
    index: ExportIndex,
    *,
    optional_outputs: tuple[str, ...] = (),
) -> IndexProjection:
    return IndexProjection(
        inputs=index.inputs,
        outputs=index.outputs,
        optional_outputs=optional_outputs,
        aliases=tuple(index.aliases),
        states={fingerprint: entry.inputs for fingerprint, entry in index.states.items()},
    )


def _fake_notebook(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    spec: ExportSpec,
) -> _FakeProducer:
    preflight = _preflight(tmp_path / "notebook.py", spec)
    producer = _FakeProducer(spec, preflight.producer)
    for module in ("plan_export", "prepare_export"):
        monkeypatch.setattr(
            f"marimo_export._services.{module}.preflight_plan",
            lambda *_args, **_kwargs: preflight,
        )
        monkeypatch.setattr(
            f"marimo_export._services.{module}.open_notebook",
            lambda *_args, **_kwargs: producer,
        )
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.require_preflight_current",
        lambda _preflight: None,
    )
    return producer


def test_a_fully_measured_projection_counts_what_the_index_writes() -> None:
    index = _measured_index()
    projection = _projection(index)
    for fingerprint, entry in index.states.items():
        projection.measure(fingerprint, entry)

    budget = projection.budget()

    assert budget.index_values == json_value_count(index.to_value())
    assert budget.index_bytes == len(index.to_bytes())


def test_an_index_of_cheapest_entries_matches_its_unmeasured_projection() -> None:
    index = _measured_index(_cheapest_entries)

    budget = _projection(index, optional_outputs=("chart",)).budget()

    assert budget.index_values == json_value_count(index.to_value())
    assert budget.index_bytes == len(index.to_bytes())


def test_an_unmeasured_projection_stays_below_the_written_index() -> None:
    index = _measured_index()

    budget = _projection(index).budget()

    assert 0 < budget.index_values < json_value_count(index.to_value())
    assert 0 < budget.index_bytes < len(index.to_bytes())


def test_an_optional_output_counts_at_most_its_unavailable_entries() -> None:
    index = _measured_index(_stopped_chart)
    written = (json_value_count(index.to_value()), len(index.to_bytes()))
    projection = _projection(index, optional_outputs=("chart",))

    budget = projection.budget()
    for fingerprint, entry in index.states.items():
        projection.measure(fingerprint, entry)
    measured = projection.budget()

    assert budget.index_values <= written[0]
    assert budget.index_bytes <= written[1]
    assert (measured.index_values, measured.index_bytes) == written
    assert _projection(index).budget().index_bytes > written[1]


def test_prepare_rejects_a_spec_that_cannot_fit_before_opening_the_notebook(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "notebook.py"
    _identity_notebook(source)
    opened: list[object] = []
    events: list[ProgressEvent] = []
    monkeypatch.setattr(budget_module, "_MAX_INDEX_VALUES", 500)
    monkeypatch.setattr(
        "marimo_export._services.prepare_export.open_notebook",
        lambda *args, **_kwargs: opened.append(args),
    )

    with (
        ExportRepository.open(tmp_path / "repository") as repository,
        pytest.raises(NotebookExportError) as raised,
    ):
        prepare(
            source,
            spec=_spec(
                states={"baseline": {"choice": "A"}}
                | {f"s{n}": {"choice": f"{n}"} for n in range(50)}
            ),
            repository=repository,
            progress=events.append,
        )

    assert raised.value.code == "export_invalid"
    assert set(raised.value.details) == {"index_values", "max_index_values"}
    assert raised.value.details["max_index_values"] == 500
    index_values = raised.value.details["index_values"]
    assert isinstance(index_values, int)
    assert index_values > 500
    assert "of the 51 states" in str(raised.value)
    assert opened == []
    assert [event.kind for event in events] == []


def test_plan_reports_the_index_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _spec(states=_THREE_STATES)
    _fake_notebook(monkeypatch, tmp_path, spec)

    with ExportRepository.open(tmp_path / "repository") as repository:
        resolved = plan(tmp_path / "notebook.py", spec=spec, repository=repository)

    budget = resolved.index_budget
    assert resolved.to_dict()["index_budget"] == budget.to_dict()
    assert budget.max_index_values == 2_000_000
    assert budget.max_index_bytes == 16 * 1024 * 1024
    assert 0 < budget.index_values < budget.max_index_values
    assert 0 < budget.index_bytes < budget.max_index_bytes


def test_prepare_stops_once_prepared_states_prove_the_index_cannot_fit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _spec(states=_THREE_STATES)
    producer = _fake_notebook(monkeypatch, tmp_path, spec)
    with ExportRepository.open(tmp_path / "repository") as repository:
        planned = plan(tmp_path / "notebook.py", spec=spec, repository=repository)
        # The plan fits by one byte. Each captured state costs more than the
        # cheapest descriptor it was planned with, so the first one breaks it.
        limit = planned.index_budget.index_bytes + 1
        monkeypatch.setattr(budget_module, "_MAX_INDEX_BYTES", limit)

        with pytest.raises(NotebookExportError) as raised:
            prepare(tmp_path / "notebook.py", spec=spec, repository=repository)
        assert raised.value.code == "export_invalid"
        assert set(raised.value.details) == {"index_bytes", "max_index_bytes"}
        assert len(producer.captured) == 1

        with pytest.raises(NotebookExportError):
            prepare(tmp_path / "notebook.py", spec=spec, repository=repository)
        assert len(producer.captured) == 1

        monkeypatch.undo()
        producer = _fake_notebook(monkeypatch, tmp_path, spec)
        resumed = plan(tmp_path / "notebook.py", spec=spec, repository=repository)
        assert len(resumed.reusable_states) == 1
        with prepare(tmp_path / "notebook.py", spec=spec, repository=repository) as prepared:
            assert len(prepared.open().states()) == 3
        assert len(producer.captured) == 2


def test_plan_counts_an_optional_output_at_its_unavailable_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    optional, required = (
        ExportSpec(
            default_state="baseline",
            states=_THREE_STATES,
            outputs={"answer": OutputSpec.json("answer", optional=flag)},
        )
        for flag in (True, False)
    )

    with ExportRepository.open(tmp_path / "repository") as repository:
        _fake_notebook(monkeypatch, tmp_path, required)
        required_bytes = plan(
            tmp_path / "notebook.py", spec=required, repository=repository
        ).index_budget.index_bytes
        _fake_notebook(monkeypatch, tmp_path, optional)
        planned = plan(tmp_path / "notebook.py", spec=optional, repository=repository)
        monkeypatch.setattr(budget_module, "_MAX_INDEX_BYTES", planned.index_budget.index_bytes)

        replanned = plan(tmp_path / "notebook.py", spec=optional, repository=repository)
        _fake_notebook(monkeypatch, tmp_path, required)
        with pytest.raises(NotebookExportError) as raised:
            plan(tmp_path / "notebook.py", spec=required, repository=repository)

    assert planned.index_budget.index_bytes < required_bytes
    assert replanned.optional_outputs == ("answer",)
    assert raised.value.code == "export_invalid"


def test_plan_rejects_resolved_states_that_the_spec_alone_cannot_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Object-valued rows can merge with a baseline object, so the spec alone
    # counts these two rows as one state. The resolved plan has two.
    spec = _spec(states={"baseline": {"choice": {"a": 1}}, "other": {"choice": {"a": 2}}})
    producer = _fake_notebook(monkeypatch, tmp_path, spec)
    planned: list[object] = []
    monkeypatch.setattr(
        producer,
        "_plan",
        lambda requested: planned.append(requested) or _plan_wire(requested, producer.producer),
    )
    with ExportRepository.open(tmp_path / "repository") as repository:
        budget = plan(tmp_path / "notebook.py", spec=spec, repository=repository).index_budget
        planned.clear()
        monkeypatch.setattr(budget_module, "_MAX_INDEX_BYTES", budget.index_bytes - 1)

        with pytest.raises(NotebookExportError) as raised:
            plan(tmp_path / "notebook.py", spec=spec, repository=repository)

    assert raised.value.code == "export_invalid"
    assert planned == [spec]


class _RecordingSession(_BorrowedSession):
    def __init__(self) -> None:
        super().__init__({}, SimpleNamespace())
        self.planned = 0

    def _plan(self, spec: ExportSpec):
        self.planned += 1
        return super()._plan(spec)


@pytest.mark.parametrize("operation", [capture_session, plan_session])
def test_capture_rejects_a_spec_that_cannot_fit_before_inspecting_the_session(
    operation: Callable[..., object],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _RecordingSession()
    monkeypatch.setattr(budget_module, "_MAX_INDEX_VALUES", 20)

    with (
        ExportRepository.open(tmp_path / "repository") as repository,
        pytest.raises(NotebookExportError) as raised,
    ):
        operation(session, spec=_spec(states=_THREE_STATES), repository=repository, timeout=5)

    assert raised.value.code == "export_invalid"
    assert session.planned == 0


def test_an_export_exactly_at_both_limits_builds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _spec(states=_THREE_STATES)
    _fake_notebook(monkeypatch, tmp_path, spec)
    with (
        ExportRepository.open(tmp_path / "sizing") as repository,
        prepare(tmp_path / "notebook.py", spec=spec, repository=repository) as sized,
    ):
        written = (sized.open().path / "index.json").read_bytes()
    values = json_value_count(json.loads(written))
    size = len(written)
    for module in (budget_module, index_module):
        monkeypatch.setattr(module, "_MAX_INDEX_VALUES", values)
        monkeypatch.setattr(module, "_MAX_INDEX_BYTES", size)

    with (
        ExportRepository.open(tmp_path / "repository") as repository,
        prepare(tmp_path / "notebook.py", spec=spec, repository=repository) as prepared,
    ):
        assert len(prepared.open().states()) == 3
