# marimo-export for Python

The Python package builds static publications from marimo notebooks, captures
existing sessions, and verifies publications from local storage.

```bash
uv add marimo-export
```

## Build

```python
from marimo_export import ExportSpec, OutputSpec, build

spec = ExportSpec(
    inputs=("symbols", "chart_width"),
    states={
        "baseline": {},
        "compact": {"chart_width": 480},
        "focus": {"symbols": ["AAPL", "MSFT"]},
    },
    outputs={
        "chart": OutputSpec(source="chart_asset"),
        "prices": OutputSpec(source="prices"),
    },
)

result = build(
    "notebook.py",
    spec=spec,
    output="dist/notebook",
)

print(result.projection_cache)
print(result.upstream_cache)
print(result.timings.total_seconds)
```

`build` starts an authenticated loopback marimo server with the current Python
interpreter. The initial autorun and state children use marimo's native cell
cache. Pending parent writes are flushed before the first state child starts.
`build` activates one session, delegates publication to the capture engine,
stops the server, and returns `PublicationResult`.

## Capture

```python
from marimo_export import ExportSpec, capture

spec = ExportSpec.from_file("notebook.export.yaml")
result = capture(
    "http://127.0.0.1:2718",
    session="SESSION_ID",
    spec=spec,
    output="dist/notebook",
)
```

`capture` borrows the selected session and leaves it running. `Client` exposes
session discovery and definition inspection when an application performs
several operations:

```python
from marimo_export import Client

with Client("http://127.0.0.1:2718") as client:
    session = client.session()
    description = session.inspect()
    result = session.capture(spec=spec, output="dist/notebook")
```

Authentication uses explicit `access_token` and `server_token` arguments or
`MARIMO_EXPORT_ACCESS_TOKEN` and `MARIMO_EXPORT_SERVER_TOKEN`.

## ExportSpec

`ExportSpec` accepts `inputs`, sparse `states`, and `outputs`. `OutputSpec`
contains one notebook definition name:

```python
spec = ExportSpec(
    inputs=("symbols_selector",),
    states={
        "baseline": {},
        "focus": {"symbols_selector": ["MSFT", "GOOGL"]},
    },
    outputs={
        "dashboard": OutputSpec(source="dashboard"),
    },
)
```

`ExportSpec.from_file(path)` reads strict UTF-8 JSON or safe YAML.
`ExportSpec.from_value(value)` validates the exact wire object.
`ExportSpec.json_schema()` generates a detached Draft 2020-12 authoring schema
on demand.

## Publication reader

```python
from marimo_export import open_publication

publication = open_publication("dist/notebook")
state = publication.state("baseline")
same_shape = state.resolve({"symbols_selector": ["MSFT", "GOOGL"]})

row_count = state.output("row_count").scalar()
arrow_bytes = state.output("prices").asset_bytes()
chart_asset = state.output("chart").blob_asset()
verification = publication.verify()
```

`open_publication` validates canonical `index.json` first. Asset methods verify
size, SHA-256, native framing, and `BlobAsset` envelope agreement before
returning data. `Publication.resolve` accepts a complete vector.
`PublishedState.resolve` applies a sparse patch to the selected state.

## Exporters

Authored Exporter functions return marimo's native `BlobAsset`:

```python
from marimo_export.exporters.altair import png, vegalite
from marimo_export.exporters.anywidget import bundle
from marimo_export.exporters.blob import html, json, text
from marimo_export.exporters.parquet import table
```

Install the dependency family used by the notebook:

```bash
uv add "marimo-export[charts,parquet,anywidget]"
```

Exporter functions are pure conversion calls with explicit options. Their
marimo cell owns dependency hashing and cache persistence.

## Public package root

The package root exports:

```text
BlobAsset
Client
ExportSpec
OutputSpec
Publication
PublicationResult
Session
build
capture
open_publication
```

Typed failures live in `marimo_export.errors`. `MarimoExportError` exposes
`code`, detached JSON `details`, and `wire()`.
