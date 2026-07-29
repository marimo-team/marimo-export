from __future__ import annotations

from pathlib import Path

from marimo_export import ExportSpec, OutputSpec, capture, open_publication
from marimo_export._remote.managed import ManagedServer


def test_ordinary_input_siblings_are_isolated_from_every_state_and_parent(
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
    x = 0
    return shared, x


@app.cell
def _(shared, x):
    shared.append(x)
    snapshot = ",".join(str(value) for value in shared)
    return (snapshot,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    source = notebook.read_bytes()
    spec = ExportSpec(
        inputs=("x",),
        states={"one": {"x": 1}, "two": {"x": 2}},
        outputs={"snapshot": OutputSpec(source="snapshot")},
    )
    server = ManagedServer(notebook, timeout=30)
    try:
        server.activate()
        first = capture(
            server.base_url,
            session=server.session_id,
            access_token=server.access_token,
            spec=spec,
            output=tmp_path / "first",
            timeout=30,
        )
        second = capture(
            server.base_url,
            session=server.session_id,
            access_token=server.access_token,
            spec=spec,
            output=tmp_path / "second",
            timeout=30,
        )
    finally:
        server.stop()

    for result in (first, second):
        publication = open_publication(result.path)
        assert publication.state("one").output("snapshot").scalar() == "0,1"
        assert publication.state("two").output("snapshot").scalar() == "0,2"
    assert notebook.read_bytes() == source
