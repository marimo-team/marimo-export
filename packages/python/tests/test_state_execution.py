from __future__ import annotations

from pathlib import Path

import pytest
from export_integration_support import (
    build,
)
from export_integration_support import (
    capture_export as _capture,
)
from export_integration_support import (
    capture_live as _capture_live,
)
from marimo_export import (
    ExportSpec,
    OutputSpec,
    open_export,
)
from marimo_export._remote.managed import ManagedServer
from marimo_export.errors import ExecutionError, SpecError
from marimo_export.exporters import importable
from marimo_export.inspection import inspect_notebook


def _prepare_with_mode(
    mode: str,
    notebook: Path,
    spec: ExportSpec,
    output: Path,
) -> None:
    if mode == "build":
        build(notebook, spec=spec, output=output, timeout=30)
        return
    _capture(notebook, spec, output)


@pytest.mark.parametrize("mode", ("build", "capture"))
def test_unrelated_stopped_branch_does_not_block_selected_output(
    tmp_path: Path,
    mode: str,
) -> None:
    notebook = tmp_path / "notebook.py"
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
    source = notebook.read_bytes()
    output = tmp_path / f"{mode}-export"

    _prepare_with_mode(
        mode,
        notebook,
        ExportSpec(
            default_state="baseline",
            states={"baseline": {}},
            outputs={"answer": OutputSpec.value("answer")},
        ),
        output,
    )

    assert open_export(output).state("baseline").output("answer").json() == 42
    assert notebook.read_bytes() == source


@pytest.mark.parametrize("mode", ("build", "capture"))
def test_selected_stopped_dependency_names_the_output_and_stopping_cell(
    tmp_path: Path,
    mode: str,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def base():
    import marimo as mo
    gate = 0
    return gate, mo


@app.cell
def guard(gate, mo):
    mo.stop(gate == 1, mo.md("waiting"))
    token = gate
    return (token,)


@app.cell
def answer(token):
    result = token + 42
    return (result,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    cells = {cell.name: cell.id for cell in inspect_notebook(notebook, timeout=30).cells}
    output = tmp_path / f"{mode}-export"

    with pytest.raises(ExecutionError) as raised:
        _prepare_with_mode(
            mode,
            notebook,
            ExportSpec(
                default_state="open",
                states={"open": {"gate": 0}, "blocked": {"gate": 1}},
                outputs={"answer": OutputSpec.value("result")},
            ),
            output,
        )

    assert raised.value.code == "output_execution_failed"
    assert raised.value.details == {
        "state": "blocked",
        "output": "answer",
        "cell_id": cells["answer"],
        "raising_cell_id": cells["guard"],
        "status": "stopped",
    }
    assert not output.exists()


def test_ordinary_input_state_is_isolated_across_states_and_captures(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    shared = []

    def record(value):
        shared.append(value)
        record.calls.append(value)

    record.calls = []

    class Bucket:
        values = []

    x = 0
    return Bucket, record, shared, x


@app.cell
def _(Bucket, record, shared, x):
    record(x)
    Bucket.values.append(x)
    snapshot = f"{shared}:{record.calls}:{Bucket.values}"
    return (snapshot,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    source = notebook.read_bytes()
    spec = ExportSpec(
        default_state="one",
        states={"one": {"x": 1}, "two": {"x": 2}},
        outputs={"snapshot": OutputSpec.value("snapshot")},
    )
    server = ManagedServer(notebook, timeout=30)
    try:
        server.activate()
        _capture_live(server, spec, tmp_path / "first")
        _capture_live(server, spec, tmp_path / "second")
    finally:
        server.stop()

    for output in (tmp_path / "first", tmp_path / "second"):
        export = open_export(output)
        assert export.state("one").output("snapshot").json() == "[1]:[1]:[1]"
        assert export.state("two").output("snapshot").json() == "[2]:[2]:[2]"
    assert notebook.read_bytes() == source


def test_ordinary_input_portability_matches_browser_safe_integer_range(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    safe_negative = -(2**53 - 1)
    safe_positive = 2**53 - 1
    unsafe_negative = -(2**53)
    unsafe_positive = 2**53
    total = safe_negative + safe_positive
    return safe_negative, safe_positive, total, unsafe_negative, unsafe_positive


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )

    definitions = {item.name: item for item in inspect_notebook(notebook, timeout=30).definitions}
    assert definitions["safe_negative"].portable_input
    assert definitions["safe_positive"].portable_input
    assert not definitions["unsafe_negative"].portable_input
    assert not definitions["unsafe_positive"].portable_input

    accepted = build(
        notebook,
        spec=ExportSpec(
            default_state="baseline",
            states={"baseline": {}},
            outputs={"total": OutputSpec.value("total")},
        ),
        output=tmp_path / "accepted",
        timeout=30,
    )
    assert open_export(accepted.path).state("baseline").output("total").json() == 0

    for name in ("unsafe_negative", "unsafe_positive"):
        with pytest.raises(SpecError) as raised:
            build(
                notebook,
                spec=ExportSpec(
                    default_state="baseline",
                    states={"baseline": {name: 0}},
                    outputs={"value": OutputSpec.value(name)},
                ),
                output=tmp_path / name,
                timeout=30,
            )
        assert raised.value.code == "spec_input_invalid"


def test_capture_preserves_the_parent_lazy_cache_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def _():
    value = 1
    return (value,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    (tmp_path / "cache_probe.py").write_text(
        """
def describe(value):
    del value
    from marimo._runtime.context import get_context
    from marimo_export.outputs import BlobAsset

    context = get_context()
    while context.parent is not None:
        context = context.parent
    loader = context.cache.active_lazy_loaders["cell_cache"]
    store = loader.store
    inner = type(getattr(store, "_inner", None)).__name__
    description = f"{type(store).__name__}:{inner}:{len(store.export_keys())}"
    return BlobAsset(data=description.encode(), media_type="text/plain")
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={
            "cache": OutputSpec.value(
                "value",
                importable("cache_probe:describe"),
            )
        },
    )
    server = ManagedServer(notebook, timeout=30)
    try:
        server.activate()
        for position in range(2):
            _capture_live(server, spec, tmp_path / f"export-{position}")
    finally:
        server.stop()

    for position in range(2):
        value = (
            open_export(tmp_path / f"export-{position}")
            .state("baseline")
            .output("cache")
            .blob_asset()
            .data.decode()
        )
        store, inner, touched = value.split(":")
        assert (store, inner) == ("LazyStore", "FileStore")
        assert int(touched) > 0
