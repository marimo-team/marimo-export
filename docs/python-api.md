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

`timings.fresh_children.ui_application_seconds` includes marimo reactive work
triggered by applying child-local UI values. `server_start_seconds` includes
session connection and kernel readiness. `initial_autorun_seconds` measures the
instantiate request through the corresponding completed run. Managed server
fields are floats for `build` and `None` for `capture`.

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
