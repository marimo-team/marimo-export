---
title: Python API reference
description: Python contracts for building, capturing, inspecting, and reading notebook exports.
---

# Python API reference

Install the producer and local reader:

```bash
uv add marimo-export
```

The package root exports `BlobAsset`, `Client`, `ExportResult`, `ExportSpec`,
`NotebookExport`, `OutputSpec`, `Session`, `build`, `capture`, and
`open_export`.

## `build`

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

Starts and executes `notebook`, prepares every ExportSpec state, writes the
notebook export, then closes the owned session and process tree.

```python
from marimo_export import ExportSpec, build

result = build(
    "finance.py",
    spec=ExportSpec.from_file("finance.export.yaml"),
    output="dist/finance",
)
```

`timeout` limits periods without progress. `replace` atomically replaces an
existing real directory on macOS and Linux.

## `capture`

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

Prepares states through an active edit session and leaves that server and
session open.

```python
from marimo_export import ExportSpec, capture

result = capture(
    "http://127.0.0.1:2718",
    session="SESSION_ID",
    spec=ExportSpec.from_file("finance.export.yaml"),
    output="dist/finance",
)
```

Omit `session` when the server has exactly one open notebook. Credentials can
come from `MARIMO_EXPORT_ACCESS_TOKEN` and `MARIMO_EXPORT_SERVER_TOKEN`.

The notebook environment must contain the same marimo-export implementation
and exporter dependencies as the client.

## `Client` and `Session`

`Client.sessions()` lists open notebooks. `Client.session(id)` returns a live
session handle. `Session.inspect()` reports definitions available as inputs or
outputs. `Session.capture()` uses the same capture contract with the selected
session already bound.

```python
from marimo_export import Client, ExportSpec

spec = ExportSpec.from_file("finance.export.yaml")
with Client("http://127.0.0.1:2718", timeout=30) as client:
    sessions = client.sessions()
    session = client.session("SESSION_ID")
    description = session.inspect()
    result = session.capture(spec=spec, output="dist/finance")
```

Each inspected definition reports whether it is a portable input and whether
state rows use `input_mode="value"` or `input_mode="patch"`.

## `ExportSpec` and `OutputSpec`

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
authoring schema on demand.

Install the exporter families used by the spec:

```bash
uv add "marimo-export[charts,parquet,anywidget]"
```

## `open_export`

```python
from marimo_export import open_export

notebook_export = open_export("dist/finance")
state = notebook_export.state("leaders")
prices = state.output("prices").blob_asset()

notebook_export.verify()
```

`NotebookExport.resolve(inputs)` selects one complete exported vector.
`ExportState.resolve(patch)` completes a sparse transition from its current
state and resolves the matching vector.

Opening validates `index.json` and keeps assets lazy. Reading an output verifies
its declared path, size, digest, native framing, and descriptor agreement.

## `ExportResult` and errors

`build`, `capture`, and `Session.capture()` return `ExportResult`. It reports
the output path, state and output counts, cache activity, timings, and bounded
warnings. `to_dict()` returns JSON-compatible values.

Typed failures live in `marimo_export.errors`. Each `MarimoExportError`
provides a stable `code`, JSON-compatible `details`, and `wire()` result.

Agents can use the same API to enumerate prepared states, read structured
outputs, and retain notebook, state, representation, and asset identity. [Use
with agents](../guide/agents-and-automation.md) defines the grounding workflow.
