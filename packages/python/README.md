# marimo-export for Python

The Python package builds static publications from marimo notebooks, captures
existing sessions, and verifies publications from local storage.

```bash
uv add marimo-export
```

## Build

```python
from marimo_export import ExportSpec, OutputSpec, build
from marimo_export.exporters import altair, parquet

spec = ExportSpec(
    inputs=("symbols", "chart_width"),
    states={
        "baseline": {},
        "compact": {"chart_width": 480},
        "focus": {"symbols": ["AAPL", "MSFT"]},
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
        "matrix": OutputSpec(source="correlation_matrix"),
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
stops the server, and returns `PublicationResult`. Exporter leaves exist only
in the in-memory state children. The source notebook stays unchanged.

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
selects one notebook definition and an optional exporter descriptor:

```python
from marimo_export import ExportSpec, OutputSpec
from marimo_export.exporters import anywidget

spec = ExportSpec(
    inputs=("symbols_selector",),
    states={
        "baseline": {},
        "focus": {"symbols_selector": ["MSFT", "GOOGL"]},
    },
    outputs={
        "dashboard": OutputSpec(
            source="quote_detail",
            exporter=anywidget.bundle(),
        ),
        "row_count": OutputSpec(source="row_count"),
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

Built-in factories construct typed exporter descriptors:

```python
from marimo_export.exporters import altair, anywidget, blob, parquet

chart = altair.vegalite()
snapshot = altair.png(scale=2)
widget = anywidget.bundle()
prices = parquet.table(compression="snappy")
document = blob.json(media_type="application/vnd.example.v1+json")
```

Install the dependency family used by the notebook:

```bash
uv add "marimo-export[charts,parquet,anywidget]"
```

Descriptor construction performs no conversion. marimo-export invokes the
selected runtime in a synthetic child leaf. marimo owns dependency hashing,
cache persistence, and native result serialization.

Custom exporters use an installed or sideloaded top-level callable:

```python
from marimo_export import OutputSpec
from marimo_export.exporters import importable

summary = OutputSpec(
    source="report",
    exporter=importable("acme_exports:summary", compact=True),
)
```

The callable receives the source value followed by the descriptor options as
keyword arguments. It returns a scalar, numeric NumPy array, supported table,
or `BlobAsset` accepted by marimo's native cache. The spec contains the import
reference and portable options.

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
