# moxport

`moxport` is a lean, typed Python client for connecting to a **running marimo notebook** over HTTP.

It is designed for:

- resolving live marimo sessions by notebook name or session id
- reading notebook source and exported script output
- parsing live source into structured marimo cell summaries
- running typed scratchpad queries against the live kernel
- treating live runtime values as remote handles via `RemoteRef`
- managing notebook/server dependencies through marimo’s own package APIs

## Install

`moxport` depends on:

- `httpx`
- `pydantic`
- `marimo`

## Happy path

```python
from moxport import MarimoClient

client = MarimoClient(timeout=30.0)
nb = client.connect("http://127.0.0.1:2718", notebook_name="02_linear_program.py")

print(nb.summary())
print(nb.get_live_source()[:300])
print(nb.get_exported_script()[:300])
print(nb.ref("1 + 1").query_json())
```

## Connection modes

You can connect by notebook name:

```python
nb = client.connect("http://127.0.0.1:2718", notebook_name="02_linear_program.py")
```

or by session id:

```python
nb = client.connect("http://127.0.0.1:2718", session_id="s_i1nf30")
```

Behavior:

- if `session_id` is given, it wins
- if `notebook_name` is given, the client resolves it against active sessions
- if multiple active sessions match, the client picks the first and emits a warning log
- if the notebook exists in the workspace but is not running, you get `NotebookNotRunningError`
- if it does not exist in the workspace, you get `NotebookNotFoundError`
- if `session_id` and `notebook_name` disagree, you get `SessionNotebookMismatchError`


## Notebook handle API

A connected notebook handle exposes:

- `session`
- `summary()`
- `get_live_source()`
- `get_exported_script()`
- `get_materialized_notebook()`
- `get_ir_summary()`
- `get_cell(target)`
- `get_materialized_output(target)`
- `runtime_variables()`
- `ref(expression)`
- `cell_ref(target)`
- `packages`

## Structured cells and live refs

`get_ir_summary()` returns typed `CellInfo` models built by parsing the live source with marimo.

```python
cells = nb.get_ir_summary()
first = cells[0]
print(first.name, first.code)

counter = nb.get_cell("counter")
print(counter.id)
```

`RemoteRef` gives you a way to query the live runtime object model without pretending arbitrary Python objects are serializable.

```python
ref = nb.ref("1 + 1")
assert ref.query_json() == 2

cell_ref = nb.cell_ref(cells[0].id)
print(cell_ref.describe())

named_ref = nb.cell_ref("counter")
print(named_ref.describe())
print(named_ref.query_json("value.count"))
```

For cell refs, `describe()` now returns a discriminated union with a top-level
`type`, such as:

- `dataframe`
- `array`
- `html`
- `widget`
- `object`

It also reports how the value was resolved:

- `retained` — existing live runtime object
- `materialized` — already computed export/materialized snapshot
- `recomputed` — scratchpad fallback when no retained object exists

## Export behavior

Two export-related APIs are provided:

- `get_exported_script()`
- `get_materialized_notebook()`

`get_materialized_notebook()` is intentionally **best effort**.

If the server-side export environment is missing a dependency like `nbformat`, the client raises a typed `ExportError` with a recovery hint.

## Package management

The notebook handle exposes a package namespace backed by marimo’s proper REST APIs:

```python
packages = nb.packages.list()
nb.packages.add("nbformat")
nb.packages.remove("nbformat")
nb.packages.install_missing("nbformat", source="server")
```

There are two package-install paths:

- `add/remove/list` use `/api/packages/*`
- `install_missing(...)` uses `/api/kernel/install_missing_packages`

Use `install_missing(..., source="server")` for cases where the **server** environment needs a dependency for an operation such as IPYNB export.

## CLI

A small CLI is also available:

```bash
moxport --server http://127.0.0.1:2718 --notebook 02_linear_program.py
moxport --server http://127.0.0.1:2718 --session-id s_i1nf30 --json
```

## Demo notebook

See `workbench/playground.py` for a live marimo notebook demo of:

- connecting to the active notebook
- showing summary and IR
- looking up a cell
- running a remote ref query
- listing packages
- handling materialized export errors cleanly

## Dev checks

```bash
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
uv run ty check src
```

Live smoke:

```bash
MOXPORT_LIVE=1 uv run pytest -q tests/test_live_smoke.py
```
