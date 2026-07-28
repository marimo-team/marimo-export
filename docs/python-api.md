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

The result records:

- absolute publication path
- `build` or `capture` mode
- borrowed session ID when applicable
- notebook filename and document SHA-256
- producer versions
- state and output names
- unique asset count and bytes
- canonical index bytes
- native cache hit and miss counts
- bounded cleanup warnings

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
