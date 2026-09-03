---
title: Sessions and inspection
description: Inspect saved notebooks, connect to live marimo sessions, plan against them, and capture prepared exports.
---

# Sessions and inspection

Use `inspect_notebook()` for a saved notebook file. Use `connect()` when a
running marimo edit session already owns the environment or completed
computation.

| Source          | Entry point                                               | Ownership after the call             |
| --------------- | --------------------------------------------------------- | ------------------------------------ |
| Saved notebook  | `inspect_notebook()`, `plan()`, `prepare()`, or `build()` | The managed process tree closes      |
| Running session | `connect()` or root `capture()`                           | The server and session remain active |

## Connect to a live server

```python
from marimo_export.sessions import connect

with connect("http://127.0.0.1:2718") as client:
    for session in client.sessions():
        print(session.id, session.filename)
```

```python
connect(
    server: str,
    *,
    access_token: str | None = None,
    server_token: str | None = None,
    timeout: float = 30.0,
) -> Client
```

`connect()` constructs a `Client`. The first request occurs when a client method
needs server data. Import `Client`, `Session`, and `connect` from
`marimo_export.sessions` for application code.

### Server URL and credential rules

`server` must use `http://` or `https://`. Plain HTTP is accepted for
`localhost` and loopback IP addresses. Use HTTPS for any other host. The URL can
contain a path prefix, but cannot contain user information, a query string, or a
fragment. Redirects are rejected.

Pass credentials as keyword arguments or set:

```text
MARIMO_EXPORT_ACCESS_TOKEN
MARIMO_EXPORT_SERVER_TOKEN
```

Explicit keyword arguments take precedence over environment values. The access
token becomes a bearer authorization header. The server token becomes the
`Marimo-Server-Token` header. Token values must be nonempty and compatible with
an HTTP header. Keep credentials out of the server URL.

The client rejects asset URLs outside the server origin and assets outside the
server's virtual-file route. Transport diagnostics redact configured token
values.

### Transport authority and timeout

Live inspection, planning, observation, and capture invoke marimo-export code
through the selected edit session's kernel endpoint. The operation therefore
has the notebook process's file, network, package, and credential authority.

`timeout` must be a positive finite number. It bounds connection work and
periods without transport progress. A long capture can exceed that duration
while individual operations keep making progress. Timed-out remote work may
still be running in the session, so inspect the session before retrying a
mutation-sensitive workflow.

The client sends each kernel operation once. It does not retry a failed execute
request because the remote kernel might already have accepted it.

## `Client`

The class constructor accepts the same arguments as `connect()`:

```python
Client(
    server: str,
    *,
    access_token: str | None = None,
    server_token: str | None = None,
    timeout: float = 30.0,
)
```

Methods:

```python
client.sessions() -> tuple[Session, ...]
client.session(session_id: str | None = None) -> Session
client.close() -> None
```

`sessions()` returns records sorted by session ID. `session(id)` returns the
exact matching session or raises `SessionError` with code `session_not_found`.

`session()` with no ID selects the only available session. It raises
`session_not_found` when none exist and `session_ambiguous` when several exist.
The ambiguous error reports up to 16 IDs in `details`.

Use `Client` as a context manager or call `close()`. Closing marks the client
closed and leaves the remote server and sessions active. Later session and
client operations raise `SessionError` with code `client_closed`.

## `Session`

A `Session` is bound to its creating `Client`. Obtain one from
`Client.session()` or `Client.sessions()`.

Properties:

```python
session.id: str
session.filename: str | None
session.path: str | None
```

Methods:

```python
session.inspect() -> SessionDescription
session.observe_inputs() -> KernelInputObservation
session.plan(
    *,
    spec: ExportSpec,
    repository: ExportRepository | None = None,
    progress: Callable[[ProgressEvent], None] | None = None,
) -> ExportPlan
session.capture(
    *,
    spec: ExportSpec,
    repository: ExportRepository | None = None,
    timeout: float = 30.0,
    progress: Callable[[ProgressEvent], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> PreparedExport
```

`plan()` inspects the live baseline and repository state without executing the
requested export states. `capture()` prepares those states and returns a leased
`PreparedExport`. Both leave the session active.

`Session.capture(timeout=...)` uses its timeout for repository reservation and
preparation waits. The `Client` timeout still controls HTTP requests, asset
downloads, and server-sent event inactivity. Cancellation is checked between
bounded phases. It cannot stop a remote scratchpad operation that is already
running.

`observe_inputs()` returns portable values for eligible live UI roots and typed
control bindings. Use it to inspect current input state. Durable observation
history uses the [repository observation APIs](repository-and-observations).

## Root `capture()`

```python
from marimo_export import ExportSpec, capture

spec = ExportSpec.from_file("report.export.yaml")

with capture(
    "http://127.0.0.1:2718",
    session="SESSION_ID",
    spec=spec,
) as prepared:
    prepared.write("dist/report", replace=True)
```

```python
capture(
    server: str,
    *,
    session: str,
    spec: ExportSpec,
    repository: ExportRepository | None = None,
    access_token: str | None = None,
    server_token: str | None = None,
    timeout: float = 30.0,
    progress: Callable[[ProgressEvent], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> PreparedExport
```

The root call creates a short-lived client, selects the required session ID,
and transfers preparation to `Session.capture()`. It closes the client before
returning the `PreparedExport`. The remote server and selected session remain
active.

The client and kernel must load the same marimo-export version and
implementation identity. A bridge package or source mismatch raises
`bridge_version_mismatch`. Source drift detected within the local client process
raises `implementation_changed`. Restart the session after changing the
installed package or an already imported custom exporter module.

`marimo_export.client.capture()` is a public wrapper for the same operation.
Use the package-root `capture()` import so producer workflows share one
documented entry point.

## Inspect a saved notebook

```python
from marimo_export.inspection import inspect_notebook

description = inspect_notebook("report.py")
print(description.input_roots())
```

```python
inspect_notebook(
    source: str | os.PathLike[str],
    *,
    timeout: float = 30.0,
) -> SessionDescription
```

File inspection creates a temporary sibling notebook copy, starts an
authenticated loopback marimo server, runs the initial autorun once, captures
the description, then closes the process tree and removes the copy. The notebook
directory must be writable. The server inherits the caller's environment and
uses the notebook directory as its working directory.

Source changes during inspection raise `ExecutionError` with code
`notebook_changed`. Startup and shutdown failures use `server_start_failed` and
`server_shutdown_failed`.

## Inspection records

### `SessionDescription`

Fields:

```python
session_id: str
filename: str | None
path: str | None
document_sha256: str
marimo_version: str
marimo_export_version: str
implementation_sha256: str
capabilities: tuple[str, ...]
definitions: tuple[DefinitionDescription, ...]
cells: tuple[CellDescription, ...]
```

Methods:

```python
description.input_roots() -> tuple[str, ...]
description.inputs_for(outputs: Mapping[str, OutputSpec]) -> tuple[str, ...]
description.to_dict() -> dict[str, object]
```

`input_roots()` returns eligible portable UI roots from the complete notebook.
`inputs_for()` returns the eligible roots that affect the selected output
dependency closures. It raises `SpecError` when a selected definition or cell is
missing or when a cell name is ambiguous.

`capabilities` lists runtime feature names reported by the inspected kernel.
Plan and capture requests perform their own bridge capability checks before
executing the requested operation.

### `DefinitionDescription`

Each immutable definition record exposes:

```python
name: str
cell_id: str
python_type: str
kind: Literal["ordinary", "ui"]
input_mode: Literal["value", "patch"]
siblings: tuple[str, ...]
portable_input: bool
sensitive: bool
value_available: bool
control_paths: Mapping[str, tuple[ControlPathStep, ...]]
input_dependencies: tuple[str, ...]
value: FrozenJsonValue | None
domain: FrozenJsonObject
```

`value` is `None` when `value_available` is false. A real observed value can
also be JSON `null`, which is represented by the same Python value. Check
`value_available` when the distinction affects a decision. `to_dict()` returns
the complete mutable wire shape.

- `siblings` lists every definition created by the same cell, including this
  definition.
- `input_mode` is `value` for complete replacement and `patch` for a sparse
  AnyWidget trait patch.
- `control_paths` maps projection-scoped control IDs to typed paths inside this
  root input.
- `domain` contains portable control hints such as options, minimum, maximum,
  step, or debounce behavior. It describes the observed control and does not
  replace producer-side validation.

### `CellDescription`

Each immutable cell record exposes:

```python
id: str
name: str | None
code_sha256: str
input_dependencies: tuple[str, ...]
config: FrozenJsonObject
```

`to_dict()` returns the complete mutable wire shape. Use `id` with
`OutputSpec.cell(id=...)`. Use a unique authored `name` with
`OutputSpec.cell(name)`.

`config` is the canonical portable marimo cell configuration captured during
inspection. `code_sha256` identifies the authored cell code while
`input_dependencies` lists upstream definition names.

## Narrow `OwnedNotebook` handle

`marimo_export.producer.open_notebook()` exposes the owned inspection context
used by file-backed producer operations:

```python
OwnedNotebook(
    notebook: str | os.PathLike[str],
    *,
    timeout: float = 30.0,
)
open_notebook(
    notebook: str | os.PathLike[str],
    *,
    timeout: float = 30.0,
) -> OwnedNotebook
```

Enter the returned context before calling `producer.inspect()`. The context is
single-use and exposes its authored `path`. Exiting closes the managed process
tree and removes the temporary copy.

The public handle supports inspection. High-level planning and capture belong
to `plan()`, `prepare()`, and `build()`. Those calls preserve repository reuse,
leases, cancellation, and complete-export commit behavior.

Use [Produce an export](produce) for file-backed preparation. Use [Host
integration](host-integration) when an application already owns a marimo
kernel context.
