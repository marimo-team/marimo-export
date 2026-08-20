from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import marimo as mo
import marimo_export.integration as integration
import pytest
from marimo_export import (
    ExportSpec,
    OutputSpec,
    build,
    open_export,
    plan,
)
from marimo_export._remote.managed import ManagedServer
from marimo_export.errors import ExecutionError
from marimo_export.index import ControlBinding, ControlIndexStep
from marimo_export.inspection import SessionDescription, inspect_notebook
from marimo_export.integration import (
    KernelInputObservation,
    is_owned_session,
    observe_kernel_inputs,
)
from marimo_export.producer import open_notebook
from marimo_export.repository import ExportRepository
from marimo_export.sessions import Client


def test_integration_module_exposes_supported_host_capabilities() -> None:
    assert integration.__all__ == [
        "KernelInputObservation",
        "is_owned_session",
        "keep_cached_cells_compatible",
        "observe_kernel_inputs",
    ]


def _notebook(path: Path) -> Path:
    path.write_text(
        """\
import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as mo
    from marimo_export.integration import is_owned_session

    owned = is_owned_session()
    owned_control = mo.ui.checkbox(value=owned)
    return owned, owned_control


if __name__ == "__main__":
    app.run()
""",
        encoding="utf-8",
    )
    return path


def _spec() -> ExportSpec:
    return ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={"owned": OutputSpec.value("owned")},
    )


def _inspected_owned_value(description: SessionDescription) -> bool:
    control = next(item for item in description.definitions if item.name == "owned_control")
    return cast(bool, control.value)


def test_owned_session_signal_covers_file_producers_without_parent_leak(
    tmp_path: Path,
) -> None:
    notebook = _notebook(tmp_path / "notebook.py")
    assert is_owned_session() is False

    description = inspect_notebook(notebook)
    assert _inspected_owned_value(description) is True
    assert is_owned_session() is False

    built = build(
        notebook,
        spec=_spec(),
        output=tmp_path / "build",
    )
    assert open_export(built.path).state("baseline").output("owned").json() is True
    assert is_owned_session() is False

    with open_notebook(notebook) as producer:
        assert _inspected_owned_value(producer.inspect()) is True
        with (
            ExportRepository.open(tmp_path / "capture-repository") as repository,
            producer._require_open().capture(spec=_spec(), repository=repository) as prepared,
        ):
            prepared.write(tmp_path / "capture")
    assert open_export(tmp_path / "capture").state("baseline").output("owned").json() is True
    assert is_owned_session() is False


def test_file_inspection_rejects_a_failed_initial_autorun(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "invalid.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as mo
    invalid = mo.ui.dropdown(
        options={"Europe": "emea"},
        value="emea",
    )
    return (invalid,)


@app.cell
def _():
    stable = 42
    return (stable,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ExecutionError) as raised:
        inspect_notebook(notebook, timeout=30)

    assert raised.value.code == "state_execution_failed"
    details = raised.value.details
    assert isinstance(details["cell_id"], str)
    assert details["exception_type"] == "ValueError"
    assert details["status"] == "exception"


def test_file_inspection_accepts_an_unrelated_stopped_branch(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "stopped.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def base():
    import marimo as mo
    answer = 42
    return answer, mo


@app.cell
def guard(mo):
    mo.stop(True, mo.md("waiting"))
    unused = 1
    return (unused,)


@app.cell
def ignored(unused):
    ignored_value = unused + 1
    return (ignored_value,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )

    description = inspect_notebook(notebook, timeout=30)

    definitions = {item.name for item in description.definitions}
    assert "answer" in definitions
    assert "unused" not in definitions
    assert "ignored_value" not in definitions


def test_file_inspection_and_plan_see_ui_created_by_initial_autorun(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as mo
    dataset_picker = mo.ui.dropdown(
        options={"Two Moons": "moons", "Concentric Circles": "circles"},
        value="Two Moons",
    )
    return (dataset_picker,)


@app.cell
def _(dataset_picker):
    selected = dataset_picker.value
    return (selected,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {"dataset_picker": ["Two Moons"]}},
        outputs={"selected": OutputSpec.value("selected")},
    )

    description = inspect_notebook(notebook, timeout=30)
    definitions = {item.name: item for item in description.definitions}
    assert definitions["dataset_picker"].kind == "ui"
    assert definitions["dataset_picker"].value == ("Two Moons",)

    with ExportRepository.open(tmp_path / "repository") as repository:
        resolved = plan(
            notebook,
            spec=spec,
            repository=repository,
            timeout=30,
        )

    assert resolved.inputs == ("dataset_picker",)
    assert resolved.states[0].inputs == {"dataset_picker": ("Two Moons",)}


def test_kernel_input_observation_is_immutable_and_binding_complete() -> None:
    values = {"scale": [2]}
    bindings = {
        "control": ControlBinding("scale", (ControlIndexStep(0),)),
    }

    observed = KernelInputObservation(values, bindings)
    values["scale"] = [9]
    bindings.clear()

    assert observed.values == {"scale": (2,)}
    assert observed.control_bindings == {"control": ControlBinding("scale", (ControlIndexStep(0),))}
    with pytest.raises(TypeError):
        cast(dict[str, object], observed.values)["other"] = 1


def test_live_input_observation_does_not_serialize_ordinary_globals() -> None:
    slider = mo.ui.slider(0, 10, value=2)

    class ExplodingList(list[object]):
        def __iter__(self) -> Iterator[object]:
            raise AssertionError("ordinary value was serialized")

    large_nonportable = ExplodingList([object()] * 100_000)
    cells = {
        "ui-cell": SimpleNamespace(defs={"scale"}, refs=set()),
        "ordinary-cell": SimpleNamespace(defs={"large_nonportable"}, refs=set()),
    }

    class Graph:
        def __init__(self) -> None:
            self.definitions = {"large_nonportable", "scale"}
            self.cells = cells
            self.parents = {"ui-cell": set(), "ordinary-cell": set()}
            self.children = {"ui-cell": set(), "ordinary-cell": set()}

        @staticmethod
        def get_defining_cells(name: str) -> set[str]:
            return {"ui-cell" if name == "scale" else "ordinary-cell"}

    observed = observe_kernel_inputs(
        SimpleNamespace(
            graph=Graph(),
            globals={"scale": slider, "large_nonportable": large_nonportable},
        )
    )

    assert observed.values == {"scale": 2}


def test_remote_input_observation_skips_large_ordinary_global(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "inputs.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    class ExplodingList(list):
        def __iter__(self):
            raise AssertionError("ordinary value was serialized")

    large_ordinary = ExplodingList([1] * 100_000)
    return (large_ordinary,)


@app.cell
def _():
    import marimo as mo
    scale = mo.ui.slider(0, 10, value=2)
    return (scale,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    server = ManagedServer(notebook, timeout=30)
    try:
        server.activate()
        with Client(
            server.base_url,
            access_token=server.access_token,
            timeout=30,
        ) as client:
            observed = client.session(server.session_id).observe_inputs()
    finally:
        server.stop()

    assert observed.values == {"scale": 2}


def test_remote_input_observation_excludes_javascript_unsafe_ui_numbers(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    import anywidget
    import marimo as mo
    import traitlets

    class Counter(anywidget.AnyWidget):
        value = traitlets.Int(2**53).tag(sync=True)

    safe = mo.ui.number(value=2**53 - 1)
    unsafe = mo.ui.number(value=2**53)
    widget = mo.ui.anywidget(Counter())
    return safe, unsafe, widget


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    definitions = {item.name: item for item in inspect_notebook(notebook, timeout=30).definitions}
    assert definitions["safe"].portable_input
    assert not definitions["unsafe"].portable_input
    assert not definitions["widget"].portable_input

    server = ManagedServer(notebook, timeout=30)
    try:
        server.activate()
        with Client(
            server.base_url,
            access_token=server.access_token,
            timeout=30,
        ) as client:
            observed = client.session(server.session_id).observe_inputs()
    finally:
        server.stop()

    assert observed.values == {"safe": 2**53 - 1}
    assert {binding.input for binding in observed.control_bindings.values()} == {"safe"}


def test_live_input_observation_uses_canonical_ui_security_and_paths() -> None:
    import anywidget
    import traitlets

    class Counter(anywidget.AnyWidget):
        value = traitlets.Int(3).tag(sync=True)

    child = mo.ui.slider(0, 10, value=2)
    form = mo.ui.text(value="ready").form()
    parent = mo.ui.array([child])
    sensitive_group = mo.ui.dictionary(
        {
            "portable": mo.ui.slider(0, 5, value=1),
            "secret": mo.ui.text(kind="password", value="private"),
        }
    )
    values = {
        "alias": child,
        "child": child,
        "form": form,
        "parent": parent,
        "portable_child": sensitive_group["portable"],
        "sensitive_group": sensitive_group,
        "widget": mo.ui.anywidget(Counter()),
    }
    owners = {
        "alias": "shared",
        "child": "shared",
        "form": "form",
        "parent": "parent",
        "portable_child": "portable-child",
        "sensitive_group": "sensitive-group",
        "widget": "widget",
    }
    cells = {
        cell_id: SimpleNamespace(
            defs={name for name, owner in owners.items() if owner == cell_id},
            refs=set(),
        )
        for cell_id in set(owners.values())
    }

    class Graph:
        def __init__(self) -> None:
            self.definitions = set(values)
            self.cells = cells
            self.parents = {cell_id: set() for cell_id in cells}
            self.children = {cell_id: set() for cell_id in cells}

        @staticmethod
        def get_defining_cells(name: str) -> set[str]:
            return {owners[name]}

    observed = observe_kernel_inputs(SimpleNamespace(graph=Graph(), globals=values))
    bindings = sorted(
        (binding.input, [step.to_value() for step in binding.path])
        for binding in observed.control_bindings.values()
    )

    assert tuple(observed.values) == ("alias", "form", "parent", "widget")
    assert observed.values["form"] is None
    assert bindings == [
        ("alias", []),
        ("form", []),
        ("form", [{"kind": "element"}]),
        ("parent", []),
        ("parent", [{"kind": "index", "value": 0}]),
        ("widget", []),
    ]
