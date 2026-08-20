---
title: Python API reference
description: Python contracts for planning, preparing, writing, inspecting, and reading notebook exports.
---

# Python API reference

Install the producer and reader:

```bash
uv add marimo-export
```

The package root exposes the common export workflow:

```python
from marimo_export import (
    ExportPlan,
    ExportRepository,
    ExportResult,
    ExportSpec,
    NotebookExport,
    OutputSpec,
    PreparedExport,
    ProgressEvent,
    VerificationResult,
    build,
    capture,
    open_export,
    plan,
    prepare,
    verify_export,
)
```

Session clients, application delivery, output values, portable JSON,
observations, inspection, and diagnostics use focused modules described in
this reference.

## `ExportSpec` and `OutputSpec`

```python
from marimo_export import ExportSpec, OutputSpec
from marimo_export.exporters import altair, parquet

spec = ExportSpec(
    default_state="baseline",
    states={
        "baseline": {},
        "weekly": {"interval": "1wk"},
    },
    outputs={
        "summary": OutputSpec.value("report.summary"),
        "report": OutputSpec.output("report.view"),
        "summary_cell": OutputSpec.cell("summary_cell"),
        "chart": OutputSpec.value("performance", altair.vegalite()),
        "prices": OutputSpec.value(
            "selected_prices",
            parquet.table(filename="prices.parquet"),
        ),
    },
)
```

`ExportSpec` contains `default_state`, `states`, and `outputs`. Input definitions
are inferred during planning. `from_file()` reads strict JSON or safe YAML.
`from_value()` validates a wire value. `to_value()` returns detached mutable
data. `json_schema()` returns the Draft 2020-12 authoring schema.

`OutputSpec.value(selector)` stores portable JSON or applies one exporter.
`OutputSpec.output(selector)` stores a formatted Marimo output snapshot.
`OutputSpec.cell(name)` stores one complete named cell. Pass `id=` to select an
inspected runtime cell ID.

## `plan`

```python
def plan(
    source,
    *,
    spec: ExportSpec,
    repository: ExportRepository | None = None,
    timeout: float = 30.0,
    progress: Callable[[ProgressEvent], None] | None = None,
) -> ExportPlan: ...
```

Resolves normalized states and repository reuse before preparation.

```python
from marimo_export import ExportSpec, plan

resolved = plan(
    "finance.py",
    spec=ExportSpec.from_file("finance.export.yaml"),
)

print(resolved.default_alias)
print(resolved.reusable_states)
print(resolved.missing_states)
```

An exact repository match sets `exact_reuse=True` and avoids notebook startup.
Cold planning executes the notebook's initial autorun to inspect its baseline and
dependencies. `timeout` bounds notebook startup and inactivity.

### `ExportPlan`

`ExportPlan` is immutable and exposes:

- `identity`
- `document_sha256`, `producer_sha256`, `output_plan_sha256`, and `spec_sha256`
- `default_alias` and `default_fingerprint`
- inferred `inputs`, normalized `states`, and `outputs`
- `reusable_states` and `missing_states` as fingerprint tuples
- `observation_revision` and projected repository `observations`
- `exact_reuse`

Each `PlannedState` in `states` exposes sorted aliases, complete immutable
inputs, and its fingerprint. `state_fingerprints` returns every normalized
fingerprint. `to_dict()` returns portable machine data.

## `prepare`

```python
def prepare(
    source,
    *,
    spec: ExportSpec,
    repository: ExportRepository | None = None,
    timeout: float = 30.0,
    progress: Callable[[ProgressEvent], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> PreparedExport: ...
```

Prepares missing states from a notebook file and returns a leased immutable
export. Use the handle as a context manager:

```python
from marimo_export import ExportSpec, prepare

spec = ExportSpec.from_file("finance.export.yaml")

with prepare("finance.py", spec=spec) as prepared:
    export = prepared.open()
    result = prepared.write("dist/finance", replace=True)
```

When `repository` is omitted, the handle owns the repository it opened and
closes it during handle teardown. A caller-supplied repository remains caller
owned. `cancelled` is checked between bounded preparation phases.

## `PreparedExport`

`PreparedExport` exposes:

```python
prepared.identity: str
prepared.plan: ExportPlan
prepared.path: Path
prepared.reused: bool
prepared.prepared_states: tuple[str, ...]
prepared.reused_states: tuple[str, ...]
prepared.cache_activity: CacheActivity
```

### `open()`

Returns a local `NotebookExport` while the parent lease remains active.

### `asset(relative)`

Returns an independently leased `PreparedAsset` for one declared export file.
The asset handle exposes `path`, `size`, `read_bytes()`, and idempotent `close()`.
Use this method when an HTTP response can outlive the parent request scope.

```python
with prepared.asset("index.json") as asset:
    response_body = asset.read_bytes()
```

### `manifest(export_url, *, state=None, refresh_interval_ms=None)`

Returns a `marimo-export.prepared.v1` browser manifest. `state` accepts an alias,
a complete input mapping, or `None` for the export default. A refresh interval is
zero or an integer from 250 through 60,000 milliseconds.

### `write(output, *, replace=False, progress=None)`

Copies, verifies, and atomically commits the prepared export, then returns an
`ExportResult`.

### `renew()` and `close()`

`renew()` validates and extends the underlying lease. `close()` is idempotent.
Calls that need files raise `RepositoryError` after close or lease loss.

## Application directory delivery

`marimo_export.delivery.stage()` owns one sibling staging directory and commits
it as a complete application directory. Write application files through
`staged.path`, then materialize each prepared export at its deployed relative
path:

```python
from pathlib import Path

from marimo_export.delivery import stage

Path("dist").mkdir(exist_ok=True)

with prepare("finance.py", spec=spec) as prepared:
    with stage("dist/site", replace=True) as staged:
        staged.path.joinpath("index.html").write_text(
            "<main id='app'></main>",
            encoding="utf-8",
        )
        staged.materialize(prepared, "data/finance")
        delivered = staged.commit()

print(delivered.path)
```

`materialize(prepared, at)` delegates the nested export write and verification
to `PreparedExport`. `commit(guard=None)` verifies each nested export again,
rejects symbolic links and special files in the outer tree, runs the optional
guard, and installs the complete directory. The context removes an uncommitted
staging directory. The destination parent must exist.

## `build`

```python
def build(
    notebook,
    *,
    spec: ExportSpec,
    output,
    repository: ExportRepository | None = None,
    timeout: float = 30.0,
    replace: bool = False,
    progress: Callable[[ProgressEvent], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ExportResult: ...
```

Validates the destination, calls `prepare()`, writes the prepared export, and
closes the handle.

```python
from marimo_export import ExportSpec, build

result = build(
    "finance.py",
    spec=ExportSpec.from_file("finance.export.yaml"),
    output="dist/finance",
)
```

## `capture`

```python
def capture(
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
) -> PreparedExport: ...
```

Prepares through one named live session and leaves the server and session active.
`timeout` is a positive finite connection and inactivity budget. It also bounds
reservation acquisition and each repository operation. A multi-state capture
can run longer than `timeout` while its individual operations keep making
progress.

```python
from marimo_export import ExportSpec, capture

spec = ExportSpec.from_file("finance.export.yaml")

with capture(
    "http://127.0.0.1:2718",
    session="SESSION_ID",
    spec=spec,
) as prepared:
    prepared.write("dist/finance", replace=True)
```

Credentials can come from `MARIMO_EXPORT_ACCESS_TOKEN` and
`MARIMO_EXPORT_SERVER_TOKEN`.

## `ExportRepository`

```python
from marimo_export import ExportRepository

with ExportRepository.open(".exports") as repository:
    status = repository.status()
    preview = repository.prune(dry_run=True)
```

`ExportRepository.open()` uses `MARIMO_EXPORT_REPOSITORY` or the operating
system cache directory when no path is supplied. The repository owns observed
input vectors, prepared state artifacts, immutable export generations,
reservations, leases, recovery, and retention.

Common methods:

- `default_path()` returns the configured path without creating it.
- `record_observation(plan, inputs)` persists one complete plan-shaped vector.
- `observation_revision(plan)` returns the producer revision for the plan.
- `observations(plan)` returns revisioned vectors projected to the plan inputs.
- `clear_observations(plan)` clears the observations associated with the plan.
- `prepared(plan)` returns an exact leased `PreparedExport`, or `None`.
- `status()` returns counts, bytes, and active leases.
- `prune(dry_run=False)` applies retention while preserving active leases.
- `close()` is idempotent.

The caller owns a supplied repository and each handle returned by `prepared()`:

```python
from marimo_export import ExportRepository, ExportSpec, plan

spec = ExportSpec.from_file("finance.export.yaml")

with ExportRepository.open(".exports") as repository:
    resolved = plan("finance.py", spec=spec, repository=repository)
    exact = repository.prepared(resolved)
    if exact is not None:
        with exact:
            exact.write("dist/finance", replace=True)
```

Repository errors and records are exported from `marimo_export.repository`.

## Live sessions

```python
from marimo_export.sessions import connect

with connect("http://127.0.0.1:2718") as client:
    sessions = client.sessions()
    session = client.session("SESSION_ID")
    description = session.inspect()
    resolved = session.plan(spec=spec)
    with session.capture(spec=spec) as prepared:
        prepared.write("dist/finance", replace=True)
```

`Client`, `Session`, and `connect()` live in `marimo_export.sessions`.
`Session.observe_inputs()` returns portable live UI roots and control bindings.
`connect(timeout=...)` configures transport inactivity. The
`Session.capture(timeout=...)` value bounds reservation acquisition and each
repository operation for that capture.

Use `marimo_export.inspection.inspect_notebook()` to inspect a saved notebook.
It executes the notebook's initial autorun and returns `SessionDescription`.

## Read and verify exports

```python
from marimo_export import open_export, verify_export

export = open_export("dist/finance")
state = export.default_state
summary = state.output("summary").json()
verified = verify_export("dist/finance")
```

`NotebookExport.resolve(inputs)` selects a complete vector.
`ExportState.resolve(patch)` resolves a sparse transition. Assets remain lazy
until an output reader or verification requests them.

`NotebookExport.identity` is the exact index SHA-256. `spec_sha256` identifies
the authored spec. `default_state` resolves the index's default fingerprint.

## Progress and results

`ProgressEvent` contains `kind`, optional counts and state, cache activity,
elapsed seconds, and an optional message. Kinds are:

```text
inspection_started
plan_ready
prepared_reused
state_started
state_finished
prepared_committed
write_finished
```

`ExportResult` reports the written path and identity, resolved plan, exact reuse,
prepared and reused state fingerprints, cache activity, asset facts,
verification result, warnings, and elapsed write time. `to_dict()` returns the
same contract as CLI JSON.

## Focused output, wire, observation, and diagnostic APIs

Custom exporters return `marimo_export.outputs.BlobAsset`.

Portable JSON functions live in `marimo_export.wire`:

```python
from marimo_export.wire import (
    canonical_json_bytes,
    canonical_json_sha256,
    parse_canonical_json,
    portable_json,
    state_fingerprint,
)
```

`marimo_export.observations` exports `ObservedInputs`, `ObservationLedger`,
`install_observation_ledger()`, and typed ingestion failures for host
integrations that record successful notebook runs.

Check the pinned Marimo adapter through diagnostics:

```python
from marimo_export.diagnostics import marimo_compatibility

check = marimo_compatibility()
if check.status == "fail":
    print(check.message, check.details)
```

## Errors

Typed failures live in `marimo_export.errors`. Each `MarimoExportError` exposes
a stable `code`, portable `details`, and `wire()` result. Repository errors live
in `marimo_export.repository` and follow the same base error contract.
