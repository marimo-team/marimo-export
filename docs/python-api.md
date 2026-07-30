# Python API

Use the Python API from scripts, jobs, and build systems.

```bash
uv add marimo-export
```

## Build from a notebook file

```python
from marimo_export import ExportSpec, build

result = build(
    "finance.py",
    spec=ExportSpec.from_file("finance.export.yaml"),
    output="dist/finance",
)
```

```python
def build(
    notebook,
    *,
    spec: ExportSpec,
    output,
    timeout: float = 30.0,
    replace: bool = False,
) -> ExportResult: ...
```

`build` prepares every state, writes the export, and closes its notebook
session. `timeout` limits periods without progress. `replace` atomically
replaces an existing directory on macOS and Linux.

## Capture an open notebook

```python
from marimo_export import ExportSpec, capture

result = capture(
    "http://127.0.0.1:2718",
    session="SESSION_ID",
    spec=ExportSpec.from_file("finance.export.yaml"),
    output="dist/finance",
)
```

```python
def capture(
    server: str,
    *,
    spec: ExportSpec,
    output,
    session: str | None = None,
    access_token: str | None = None,
    server_token: str | None = None,
    timeout: float = 30.0,
    replace: bool = False,
) -> ExportResult: ...
```

Capture leaves the session open. Omit `session` when the server has exactly one
open notebook.

Credentials can come from `MARIMO_EXPORT_ACCESS_TOKEN` and
`MARIMO_EXPORT_SERVER_TOKEN`. The notebook environment must provide the same
marimo-export version and exporter dependencies.

## Inspect sessions

```python
from marimo_export import Client, ExportSpec

spec = ExportSpec.from_file("finance.export.yaml")
with Client("http://127.0.0.1:2718", timeout=30) as client:
    sessions = client.sessions()
    session = client.session("SESSION_ID")
    notebook = session.inspect()
    result = session.capture(spec=spec, output="dist/finance")
```

`Client.sessions()` lists open notebooks. `Session.inspect()` lists definitions
available as ExportSpec inputs or outputs.

## Construct a spec

```python
from marimo_export import ExportSpec, OutputSpec
from marimo_export.exporters import altair, parquet

spec = ExportSpec(
    inputs=("symbols_selector",),
    states={
        "leaders": {},
        "cloud": {"symbols_selector": ["MSFT", "GOOGL", "AMZN"]},
    },
    outputs={
        "chart": OutputSpec(
            source="performance",
            exporter=altair.vegalite(),
        ),
        "prices": OutputSpec(
            source="selected_prices",
            exporter=parquet.table(filename="prices.parquet"),
        ),
    },
)
```

`ExportSpec.from_file()` reads YAML or JSON. `from_value()` validates a Python
value. `to_value()` returns plain Python data. `json_schema()` returns the
authoring schema.

Install the exporter families used by the spec:

```bash
uv add "marimo-export[charts,parquet,anywidget]"
```

## Read a finished export

```python
from marimo_export import open_export

notebook_export = open_export("dist/finance")
state = notebook_export.state("leaders")

prices = state.output("prices").blob_asset()
chart = state.output("chart").blob_asset()

notebook_export.verify()
```

`NotebookExport.resolve(inputs)` selects a complete input set.
`ExportState.resolve(patch)` applies a smaller change to an existing state.

## Results and errors

`build`, `capture`, and `Session.capture()` return `ExportResult`. It reports
the path, state and output counts, cache activity, and timings. `to_dict()`
returns the same data as JSON-compatible values.

Typed failures live in `marimo_export.errors`. Each `MarimoExportError`
provides a stable `code`, JSON-compatible `details`, and `wire()` result.
