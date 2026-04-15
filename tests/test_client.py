from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from moxport import (
    CellInfo,
    ExportError,
    LiveCellInfo,
    MarimoClient,
    MarimoNotebookClient,
    MaterializedCell,
    MaterializedNotebook,
    NotebookNotFoundError,
    NotebookNotRunningError,
    PackageListResult,
    PackageOperationError,
    RuntimeVariable,
    ScratchpadExecutionError,
    SessionInfo,
    SessionNotebookMismatchError,
    SessionNotFoundError,
)

MINIMAL_NOTEBOOK = (
    """
import marimo

app = marimo.App(width="medium")


@app.cell
def load_df():
    import polars as pl
    df = pl.DataFrame({"x": [1, 2], "y": [3, 4]})
    return (df,)


@app.cell
def summarize(df):
    df.head()
    return


if __name__ == "__main__":
    app.run()
""".strip()
    + "\n"
)


def make_client(handler: Callable[[httpx.Request], httpx.Response]) -> MarimoClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    return MarimoClient(client=http)


def make_notebook(
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
) -> MarimoNotebookClient:
    transport = httpx.MockTransport(
        handler or (lambda request: httpx.Response(200, json={}))
    )
    http = httpx.Client(transport=transport)
    return MarimoNotebookClient(
        "http://server",
        SessionInfo(session_id="s1", filename="main.py", path="/tmp/main.py"),
        "main.py",
        client=http,
    )


def json_response(data: object, *, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=data)


def text_response(text: str, *, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, text=text)


def test_connect_by_exact_session_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/sessions"
        return json_response(
            {
                "s1": {"filename": "a.py", "path": "/tmp/a.py"},
                "s2": {"filename": "main.py", "path": "/tmp/main.py"},
            }
        )

    with make_client(handler) as client:
        notebook = client.connect("http://server", session_id="s2")

    assert notebook.session == SessionInfo(
        session_id="s2",
        filename="main.py",
        path="/tmp/main.py",
    )


def test_connect_by_notebook_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions":
            return json_response(
                {
                    "s1": {"filename": "other.py", "path": "/tmp/other.py"},
                    "s2": {
                        "filename": "02_linear_program.py",
                        "path": "/tmp/02_linear_program.py",
                    },
                }
            )
        raise AssertionError(request.url)

    with make_client(handler) as client:
        notebook = client.connect("http://server", notebook_name="02_linear_program.py")

    assert notebook.session.session_id == "s2"


def test_multiple_name_matches_pick_first_and_log_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {
                "s_first": {"filename": "main.py", "path": "/tmp/a/main.py"},
                "s_second": {"filename": "main.py", "path": "/tmp/b/main.py"},
            }
        )

    with make_client(handler) as client:
        with caplog.at_level("WARNING"):
            notebook = client.connect("http://server", notebook_name="main.py")

    assert notebook.session.session_id == "s_first"
    assert "Multiple active sessions matched" in caplog.text
    assert "s_second" in caplog.text


def test_notebook_not_running_if_found_in_workspace() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions":
            return json_response({})
        if request.url.path == "/api/home/workspace_files":
            return json_response(
                {
                    "root": "/tmp",
                    "files": [
                        {
                            "id": "02_linear_program.py",
                            "path": "02_linear_program.py",
                            "name": "02_linear_program.py",
                            "isDirectory": False,
                            "isMarimoFile": True,
                            "children": [],
                        }
                    ],
                }
            )
        raise AssertionError(request.url)

    with make_client(handler) as client:
        with pytest.raises(NotebookNotRunningError):
            client.connect("http://server", notebook_name="02_linear_program.py")


def test_notebook_not_found_if_missing_from_workspace() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions":
            return json_response({})
        if request.url.path == "/api/home/workspace_files":
            return json_response({"root": "/tmp", "files": []})
        raise AssertionError(request.url)

    with make_client(handler) as client:
        with pytest.raises(NotebookNotFoundError):
            client.connect("http://server", notebook_name="missing.py")


def test_session_id_and_notebook_name_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response({"s1": {"filename": "other.py", "path": "/tmp/other.py"}})

    with make_client(handler) as client:
        with pytest.raises(SessionNotebookMismatchError):
            client.connect(
                "http://server",
                notebook_name="main.py",
                session_id="s1",
            )


def test_unknown_session_id_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response({})

    with make_client(handler) as client:
        with pytest.raises(SessionNotFoundError):
            client.connect("http://server", session_id="missing")


def test_get_live_source_returns_source_text(monkeypatch: pytest.MonkeyPatch) -> None:
    notebook = make_notebook()
    monkeypatch.setattr(
        notebook, "_run_json", lambda body: {"source": MINIMAL_NOTEBOOK}
    )
    assert notebook.get_live_source() == MINIMAL_NOTEBOOK


def test_get_ir_summary_parses_live_source(monkeypatch: pytest.MonkeyPatch) -> None:
    notebook = make_notebook()
    monkeypatch.setattr(notebook, "get_live_source", lambda: MINIMAL_NOTEBOOK)
    monkeypatch.setattr(
        notebook,
        "_get_live_cells",
        lambda: [
            LiveCellInfo(index=0, id="live-a", name="load_df", code="df = 1"),
            LiveCellInfo(index=1, id="live-b", name="summarize", code="df.head()"),
        ],
    )
    cells = notebook.get_ir_summary()
    assert len(cells) == 2
    assert cells[0].id == "live-a"
    assert cells[0].name == "load_df"
    assert cells[1].refs == ["df"]


def test_get_cell_by_id_name_and_index(monkeypatch: pytest.MonkeyPatch) -> None:
    notebook = make_notebook()
    cells = [
        CellInfo(index=0, id="a", name="alpha", code="x = 1", defs=["x"]),
        CellInfo(index=1, id="b", name="beta", code="x", refs=["x"]),
    ]
    monkeypatch.setattr(notebook, "get_ir_summary", lambda: cells)
    assert notebook.get_cell(0).id == "a"
    assert notebook.get_cell("b").name == "beta"
    assert notebook.get_cell("beta").id == "b"


def test_get_materialized_output_prefers_id_then_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = make_notebook()
    cells = [
        CellInfo(index=0, id="a", name="alpha", code="x = 1"),
        CellInfo(index=1, id="b", name="beta", code="x"),
    ]
    monkeypatch.setattr(notebook, "get_ir_summary", lambda: cells)
    monkeypatch.setattr(
        notebook,
        "get_materialized_notebook",
        lambda: MaterializedNotebook(
            cells=[
                MaterializedCell(id="mismatch-a", source=["x = 1"]),
                MaterializedCell(
                    id="mismatch-b",
                    source=["x"],
                    outputs=[{"output_type": "display_data"}],
                ),
            ]
        ),
    )
    entry = notebook.get_materialized_output("beta")
    assert entry.id == "mismatch-b"


def test_runtime_variables_return_queryable_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = make_notebook()
    monkeypatch.setattr(
        notebook,
        "get_ir_summary",
        lambda: [
            CellInfo(index=0, id="cid", name="load_df", code="df = 1", defs=["df"])
        ],
    )
    calls: list[str] = []

    def fake_run_json(body: str) -> object:
        calls.append(body)
        if len(calls) == 1:
            return {"df": {"type": "DataFrame", "repr": "shape: (2, 2)"}}
        return {"data": {"rows": 2}}

    monkeypatch.setattr(notebook, "_run_json", fake_run_json)
    variables = notebook.runtime_variables()
    assert isinstance(variables["df"], RuntimeVariable)
    assert variables["df"].datatype == "DataFrame"
    assert variables["df"].preview == "shape: (2, 2)"
    assert variables["df"].query_json("{'rows': value.height}") == {"rows": 2}
    assert "globals()[name]" in calls[0]


def test_get_exported_script_uses_server_token() -> None:
    seen_headers: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return text_response(
                '<script>window.__MARIMO__={"serverToken":"server-token-123"}</script>'
            )
        if request.url.path == "/api/export/script":
            seen_headers.append(dict(request.headers))
            return text_response("script one")
        raise AssertionError(request.url)

    notebook = MarimoNotebookClient(
        "http://server",
        SessionInfo(session_id="s1", filename="main.py", path="/tmp/main.py"),
        "main.py",
        token="token-123",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert notebook.get_exported_script() == "script one"
    assert seen_headers[0]["authorization"] == "Bearer token-123"
    assert seen_headers[0]["marimo-session-id"] == "s1"
    assert seen_headers[0]["marimo-server-token"] == "server-token-123"


def test_get_materialized_notebook_raises_typed_export_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return text_response(
                '<script>window.__MARIMO__={"serverToken":"server-token-123"}</script>'
            )
        if request.url.path == "/api/export/ipynb":
            return text_response(
                "ModuleNotFoundError: No module named 'nbformat'", status_code=500
            )
        raise AssertionError(request.url)

    notebook = MarimoNotebookClient(
        "http://server",
        SessionInfo(session_id="s1", filename="main.py", path="/tmp/main.py"),
        "main.py",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ExportError) as exc:
        notebook.get_materialized_notebook()
    assert 'nb.packages.install_missing("nbformat", source="server")' in str(exc.value)


def test_package_api_uses_proper_marimo_rest_endpoints() -> None:
    seen: list[tuple[str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body: object = None
        if request.content:
            body = json.loads(request.content.decode())
        seen.append((request.method, request.url.path, body))
        if request.url.path == "/api/packages/list":
            return json_response({"packages": [{"name": "httpx", "version": "0.28.1"}]})
        if request.url.path == "/api/packages/add":
            return json_response({"success": True, "error": None})
        if request.url.path == "/api/packages/remove":
            return json_response({"success": True, "error": None})
        if request.url.path == "/api/kernel/install_missing_packages":
            return json_response({"success": True})
        raise AssertionError(request.url)

    notebook = MarimoNotebookClient(
        "http://server",
        SessionInfo(session_id="s1", filename="main.py", path="/tmp/main.py"),
        "main.py",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert notebook.packages.list() == PackageListResult(
        packages=[{"name": "httpx", "version": "0.28.1"}]
    )
    notebook.packages.add("nbformat")
    notebook.packages.remove("nbformat")
    notebook.packages.install_missing("nbformat", source="server")

    assert seen[0][1] == "/api/packages/list"
    assert seen[1][1] == "/api/packages/add"
    assert seen[1][2] == {"package": "nbformat", "upgrade": False}
    assert seen[2][1] == "/api/packages/remove"
    assert seen[2][2] == {"package": "nbformat"}
    assert seen[3][1] == "/api/kernel/install_missing_packages"
    assert seen[3][2] == {
        "manager": "uv",
        "versions": {"nbformat": ""},
        "source": "server",
    }


def test_package_operation_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response({"success": False, "error": "boom"})

    notebook = MarimoNotebookClient(
        "http://server",
        SessionInfo(session_id="s1", filename="main.py", path="/tmp/main.py"),
        "main.py",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(PackageOperationError):
        notebook.packages.add("nbformat")


def test_cell_ref_describe_uses_retained_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = make_notebook()
    cell = CellInfo(index=0, id="cid", name="demo", code="widget", defs=["widget"])
    calls: list[str] = []

    def fake_run_json(body: str) -> object:
        calls.append(body)
        return {
            "kind": "cell",
            "selector": "cid",
            "type": "widget",
            "module": "marimo._output.hypertext",
            "python_type": "CounterWidget",
            "preview": "<CounterWidget>",
            "resolution": "retained",
        }

    monkeypatch.setattr(notebook, "get_cell", lambda target: cell)
    monkeypatch.setattr(notebook, "_run_json", fake_run_json)

    description = notebook.cell_ref("cid").describe()
    assert description.type == "widget"
    assert description.resolution == "retained"
    assert description.python_type == "CounterWidget"
    assert "_lookup_retained_cell_output" in calls[0]


def test_cell_ref_describe_uses_materialized_before_recompute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = make_notebook()
    cell = CellInfo(index=1, id="md1", name=None, code='mo.md("""# Hello""")')

    monkeypatch.setattr(notebook, "get_cell", lambda target: cell)
    monkeypatch.setattr(
        notebook,
        "_run_json",
        lambda body: (_ for _ in ()).throw(
            ScratchpadExecutionError("Unknown live cell id: md1")
        ),
    )
    monkeypatch.setattr(
        notebook,
        "get_materialized_output",
        lambda target: MaterializedCell(
            id="md1",
            cell_type="markdown",
            source=["# Hello\n", "\nBody"],
        ),
    )

    description = notebook.cell_ref("md1").describe()
    assert description.type == "html"
    assert description.resolution == "materialized"
    assert description.mime == "text/markdown"
    assert "Hello" in description.text
    assert "Hello" in description.preview


def test_cell_ref_describe_falls_through_to_recompute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = make_notebook()
    cell = CellInfo(index=2, id="md2", name=None, code='mo.md("""# Tail""")')
    calls: list[str] = []

    def fake_run_json(body: str) -> object:
        calls.append(body)
        if "_lookup_retained_cell_output" in body:
            raise ScratchpadExecutionError(
                "Cell 'md2' does not expose a retained output object"
            )
        return {
            "kind": "cell",
            "selector": "md2",
            "python_type": "Html",
            "module": "marimo._output.hypertext",
            "preview": "<h1>Tail</h1>",
            "text": "<h1>Tail</h1>",
            "has_dataframe_protocol": False,
            "has_array_protocol": False,
            "has_array_namespace": False,
        }

    monkeypatch.setattr(notebook, "get_cell", lambda target: cell)
    monkeypatch.setattr(notebook, "_run_json", fake_run_json)
    monkeypatch.setattr(
        notebook,
        "get_materialized_output",
        lambda target: (_ for _ in ()).throw(ExportError("boom")),
    )

    description = notebook.cell_ref("md2").describe()
    assert description.type == "html"
    assert description.resolution == "recomputed"
    assert description.mime == "text/html"
    assert any("_resolve_cell_value" in body for body in calls)


def test_cell_ref_query_json_recomputes_when_not_retained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = make_notebook()
    cell = CellInfo(index=1, id="md3", name=None, code='mo.md("""# Query""")')
    calls: list[str] = []

    def fake_run_json(body: str) -> object:
        calls.append(body)
        return {"data": "rendered markdown"}

    monkeypatch.setattr(notebook, "get_cell", lambda target: cell)
    monkeypatch.setattr(notebook, "_run_json", fake_run_json)

    result = notebook.cell_ref("md3").query_json(
        "value.text if hasattr(value, 'text') else repr(value)"
    )
    assert result == "rendered markdown"
    assert "_resolve_cell_value" in calls[0]


def test_cell_ref_describe_classifies_dataframe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = make_notebook()
    cell = CellInfo(index=0, id="df", name="frame", code="df")

    monkeypatch.setattr(notebook, "get_cell", lambda target: cell)
    monkeypatch.setattr(
        notebook,
        "_run_json",
        lambda body: {
            "kind": "cell",
            "selector": "df",
            "python_type": "DataFrame",
            "module": "polars.dataframe.frame",
            "preview": "shape: (2, 2)",
            "shape": [2, 2],
            "columns": ["x", "y"],
            "has_dataframe_protocol": True,
            "has_array_protocol": False,
            "has_array_namespace": False,
        },
    )

    description = notebook.cell_ref("df").describe()
    assert description.type == "dataframe"
    assert description.rows == 2
    assert description.cols == 2
    assert description.columns == ["x", "y"]


def test_cell_ref_describe_classifies_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = make_notebook()
    cell = CellInfo(index=0, id="arr", name="arr", code="arr")

    monkeypatch.setattr(notebook, "get_cell", lambda target: cell)
    monkeypatch.setattr(
        notebook,
        "_run_json",
        lambda body: {
            "kind": "cell",
            "selector": "arr",
            "python_type": "ndarray",
            "module": "numpy",
            "preview": "array([1, 2, 3])",
            "shape": [3],
            "ndim": 1,
            "dtype": "int64",
            "has_dataframe_protocol": False,
            "has_array_protocol": True,
            "has_array_namespace": False,
        },
    )

    description = notebook.cell_ref("arr").describe()
    assert description.type == "array"
    assert description.shape == [3]
    assert description.ndim == 1
    assert description.dtype == "int64"


def test_cell_ref_describe_falls_back_to_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = make_notebook()
    cell = CellInfo(index=0, id="obj", name="obj", code="obj")

    monkeypatch.setattr(notebook, "get_cell", lambda target: cell)
    monkeypatch.setattr(
        notebook,
        "_run_json",
        lambda body: {
            "kind": "cell",
            "selector": "obj",
            "python_type": "Thing",
            "module": "__main__",
            "preview": "<Thing()>",
            "has_dataframe_protocol": False,
            "has_array_protocol": False,
            "has_array_namespace": False,
        },
    )

    description = notebook.cell_ref("obj").describe()
    assert description.type == "object"
    assert description.preview == "<Thing()>"
