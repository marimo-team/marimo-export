# Python API

The package root exposes the composition API:

```python
from marimo_export import (
    BlobAsset,
    Client,
    ExportSpec,
    OutputSpec,
    Publication,
    PublicationResult,
    Session,
    build,
    capture,
    open_publication,
)
```

## `build`

```python
build(
    notebook,
    *,
    spec: ExportSpec,
    output,
    timeout: float = 30.0,
    replace: bool = False,
) -> PublicationResult
```

Starts an authenticated loopback server through the current interpreter,
activates one notebook session, publishes the matrix, and stops owned
processes. The source notebook digest is checked before and after execution.
Projection cells exist only in the in-memory state children.

## `capture`

```python
capture(
    server: str,
    *,
    spec: ExportSpec,
    output,
    session: str | None = None,
    access_token: str | None = None,
    server_token: str | None = None,
    timeout: float = 30.0,
    replace: bool = False,
) -> PublicationResult
```

Borrows one active session. Omitting `session` requires exactly one session.
The server and parent session remain live when capture returns.

## `Client`

```python
with Client(
    "http://127.0.0.1:2718",
    access_token=token,
    timeout=30,
) as client:
    sessions = client.sessions()
    session = client.session()
    description = session.inspect()
    result = session.capture(spec=spec, output="dist/notebook")
```

`Session.inspect()` returns definition names, cell ownership, sibling names,
Python types, UI or ordinary kind, portable-input status, sensitivity, public
baseline values, and UI domains.

## `OutputSpec` and `ExporterSpec`

```python
from marimo_export import OutputSpec
from marimo_export.exporters import ExporterSpec, altair, importable

interactive = OutputSpec(
    source="performance",
    exporter=altair.vegalite(),
)
snapshot = OutputSpec(
    source="performance",
    exporter=altair.png(scale=2),
)
custom = OutputSpec(
    source="report",
    exporter=importable("acme_exports:summary", compact=True),
)
native = OutputSpec(source="ohlc_matrix")
```

`OutputSpec(source, exporter=None)` selects one notebook definition.
`exporter=None` keeps marimo's native cache representation.

Built-in factories return an `ExporterSpec`. They do not convert a Python
object in the calling process. `importable(name, **options)` accepts a
`module:function` reference to a top-level callable in the notebook
environment. Exporter options are portable JSON values and become keyword
arguments in the transient projection cell.

`ExporterSpec.name` is the normalized built-in ID or import reference.
`ExporterSpec.options` is immutable. `to_value()` returns the normalized wire
value, and `from_value()` accepts the string shorthand or exact object form.

## `PublicationResult`

`build`, `capture`, and `Session.capture()` return the publication record and
run-local performance data:

```python
print(result.projection_cache.hits, result.projection_cache.misses)
print(result.upstream_cache.hits, result.upstream_cache.misses)
print(result.timings.total_seconds)
print(result.timings.fresh_children.construction_seconds)
```

The result records:

- absolute publication path
- `build` or `capture` mode
- borrowed session ID when applicable
- notebook filename and document SHA-256
- producer versions
- state and output names
- unique asset count and bytes
- canonical index bytes
- projection cache lookup counts
- upstream cell-cache lookup counts
- managed server, capture, publication, and total timings
- aggregated fresh-child construction, execution, UI, projection, and cleanup
  timings
- bounded cleanup warnings

`projection_cache` covers one native projection receipt per state and output.
`upstream_cache` covers native cache lookups for non-projection cells executed
in the fresh state children. A hit records a matching entry. marimo can still
run the cell when restoration fails or the cell defines session-local UI
elements.

`timings.fresh_children.ui_application_seconds` measures child-local UI value
application. `projection_execution_seconds` includes the marimo reactive work
needed to materialize those values and execute the projection cells in one
cache-aware run. `server_start_seconds` includes session connection and kernel
readiness. `initial_autorun_seconds` measures the instantiate request through
the corresponding completed run. Managed server fields are floats for `build`
and `None` for `capture`.

`to_dict()` returns a detached JSON value.

## Local publication reader

```python
publication = open_publication("dist/notebook")
state = publication.state("baseline")
output = state.output("chart")

print(output.codec, output.media_type)
publication.verify()
```

Use `scalar()` for scalar outputs, `asset_bytes()` for NPY or Arrow assets, and
`blob_asset()` for a decoded native `BlobAsset`.

`Publication.resolve(inputs)` selects a complete vector.
`PublishedState.resolve(patch)` applies a sparse patch.

Typed failures are available from `marimo_export.errors`.
