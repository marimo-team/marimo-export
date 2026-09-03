---
title: Produce an export from Python
description: Define an ExportSpec, inspect reusable work, prepare states, and write a verified notebook export.
---

# Produce an export from Python

`build()` prepares every state in an `ExportSpec` and writes one verified
notebook export. Start with portable JSON when the consumer needs structured
records, arrays, or metrics:

```python
from pathlib import Path

from marimo_export import ExportSpec, OutputSpec, build

spec = ExportSpec(
    default_state="baseline",
    states={
        "baseline": {},
        "weekly": {"interval": "1wk"},
    },
    outputs={"summary": OutputSpec.json("report.summary")},
)

Path("dist").mkdir(exist_ok=True)
result = build("report.py", spec=spec, output="dist/report")
print(result.path)
```

`build()` runs the notebook with its current environment, file, credential, and
network access. It creates a temporary sibling copy beside the notebook, so the
notebook directory must be writable. The output directory's parent must already
exist. The authored notebook remains unchanged.

## `StateSpace`

```python
StateSpace(
    *,
    default_state: str,
    states: Mapping[str, Mapping[str, JsonValue]] | None = None,
    matrix: Mapping[str, list[JsonValue]] | None = None,
)
```

A `StateSpace` declares input states independently of outputs. `states` maps
stable names to sparse input assignments. `matrix` expands the Cartesian
product of nonempty input domains into deterministic `matrix-000000` names.

```python
from marimo_export import ExportSpec, OutputSpec, StateSpace

state_space = StateSpace.from_file("states.yaml")
spec = ExportSpec.from_state_space(
    state_space,
    outputs={"summary": OutputSpec.json("report.summary")},
)
```

Methods and properties:

```python
StateSpace.from_file(path: str | os.PathLike[str]) -> StateSpace
StateSpace.from_yaml(text: str | bytes, *, source: str = "<memory>") -> StateSpace
StateSpace.from_value(value: object) -> StateSpace
StateSpace.json_schema() -> dict[str, object]
state_space.to_value() -> dict[str, object]
state_space.digest: str
ExportSpec.from_state_space(
    state_space: StateSpace,
    *,
    outputs: Mapping[str, OutputSpec],
) -> ExportSpec
```

`to_value()` returns normalized explicit states after matrix expansion. The
`digest` is the SHA-256 identity of that normalized form.

Invalid documents and state relations raise `SpecError` with code
`spec_invalid` or `spec_value_invalid`. Non-mapping state collections,
non-mapping rows, and non-list matrix domains raise `TypeError`.

## `ExportSpec`

```python
ExportSpec(
    *,
    default_state: str,
    states: Mapping[str, Mapping[str, JsonValue]],
    outputs: Mapping[str, OutputSpec],
)
```

An `ExportSpec` declares the finite relation that consumers can select:

- `default_state` names one entry in `states`.
- `states` maps authored aliases to sparse input assignments.
- `outputs` maps published names to `OutputSpec` values.

Planning infers the complete input-name set, fills omitted values from one
captured baseline, and deduplicates rows that resolve to the same complete input
vector. The immutable `states` and `outputs` mappings are sorted during
normalization.

Methods:

```python
ExportSpec.from_file(path: str | os.PathLike[str]) -> ExportSpec
ExportSpec.from_value(value: object) -> ExportSpec
ExportSpec.json_schema() -> dict[str, object]
spec.to_value() -> dict[str, object]
```

`from_file()` reads a UTF-8 `.json`, `.yaml`, or `.yml` file up to 16 MiB. It
rejects duplicate keys. YAML aliases and merge keys are invalid. `from_value()`
accepts an existing `ExportSpec` or validates the exact wire object.
`json_schema()` returns the Draft 2020-12 authoring schema. `to_value()` returns
detached mutable data.

Invalid wire values raise `SpecError`. Its code identifies the affected part as
`spec_invalid`, `spec_value_invalid`, `spec_output_invalid`, or
`spec_exporter_invalid`.

The [StateSpace and ExportSpec reference](../export-spec.md) defines state
values, matrix expansion, selector syntax, and the YAML and JSON shapes.

## `OutputSpec`

Use the factory that matches the stored representation:

```python
OutputSpec.json(selector: str) -> OutputSpec
OutputSpec.native(selector: str) -> OutputSpec
OutputSpec.export(selector: str, exporter: ExporterSpec) -> OutputSpec
OutputSpec.output(selector: str) -> OutputSpec
OutputSpec.cell(name: str | None = None, *, id: str | None = None) -> OutputSpec
```

| Factory    | Stored output                                                                                  |
| ---------- | ---------------------------------------------------------------------------------------------- |
| `json()`   | Canonical portable JSON selected from a notebook definition                                    |
| `native()` | marimo cache representation for a scalar, JSON value, NumPy array, Arrow table, or `BlobAsset` |
| `export()` | `BlobAsset` returned by an explicit exporter                                                   |
| `output()` | Formatted `marimo.output.v1` snapshot and replay resources                                     |
| `cell()`   | Complete `marimo.cell.v1` snapshot selected by authored cell name or inspected runtime ID      |

`cell()` requires exactly one of `name` or `id`. Selected-value factories parse
the selector during construction. Invalid selectors and cell references raise
`SpecError` with a `spec_output_invalid` code.

`OutputSpec.source` exposes the normalized source record for inspection. Its
concrete source-record classes are not public construction helpers. Construct an
output through the five factories and use `ExportSpec.to_value()` when code
needs the portable source shape.

## Built-in exporters

Typed factories return immutable `ExporterSpec` values. Install the matching
producer extra before preparing the export.

```python
from marimo_export.exporters import altair, anywidget, blob, parquet
```

| Factory                                                                              | Defaults                                             | Producer extra |
| ------------------------------------------------------------------------------------ | ---------------------------------------------------- | -------------- |
| `altair.vegalite()`                                                                  | No options                                           | `charts`       |
| `altair.png(*, scale=1.0)`                                                           | Positive finite scale                                | `charts`       |
| `anywidget.bundle()`                                                                 | No options                                           | `anywidget`    |
| `parquet.table(*, compression="snappy", filename=None)`                              | `snappy`, `none`, `gzip`, `brotli`, `lz4`, or `zstd` | `parquet`      |
| `blob.json(*, media_type="application/json", filename=None, metadata=None)`          | Canonical JSON bytes                                 | Base package   |
| `blob.text(*, media_type="text/plain; charset=utf-8", filename=None, metadata=None)` | UTF-8 text                                           | Base package   |
| `blob.html(*, filename=None, metadata=None)`                                         | UTF-8 HTML                                           | Base package   |

`marimo_export.exporters.parquet.Compression` is the type alias for the six
accepted Parquet compression strings.

`filename` must become a portable basename when the exporter returns its
`BlobAsset`. `metadata` must be a portable JSON object. Missing optional Python
distributions raise an output failure with code
`runtime_distribution_unavailable`.

### `ExporterSpec` and `importable()`

```python
from marimo_export.exporters import ExporterSpec, importable

exporter = importable(
    "market_summary:encode",
    options={"currency": "USD"},
    dependencies=("market_summary.formatting",),
)
```

```python
ExporterSpec(
    name: str,
    *,
    options: Mapping[str, JsonValue] | None = None,
    dependencies: tuple[str, ...] = (),
)
ExporterSpec.from_value(value: object) -> ExporterSpec
exporter.to_value() -> JsonValue
importable(
    name: str,
    *,
    options: Mapping[str, JsonValue] | None = None,
    dependencies: tuple[str, ...] = (),
) -> ExporterSpec
```

A custom name uses `module:symbol`. Option keys must be non-keyword Python
identifiers. `dependencies` contains at most 256 sorted, unique importable module
names whose source affects the returned bytes. Custom exporter calls run for
every state that needs preparation. A live session keeps already imported
modules, so restart it after changing custom exporter source.

### `BlobAsset`

Custom exporters return `marimo_export.outputs.BlobAsset`:

```python
from marimo_export.outputs import BlobAsset


def encode(value: str) -> BlobAsset:
    return BlobAsset(
        data=value.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        filename="summary.txt",
        metadata={"schema": "example.summary.v1"},
    )
```

```python
BlobAsset(
    *,
    data: bytes,
    media_type: str | None = None,
    filename: str | None = None,
    metadata: Mapping[str, object] | None = None,
)
```

`data` must be `bytes`. `filename` must be a portable basename. `metadata` is
copied, normalized, and exposed as recursively immutable portable JSON. Its
canonical encoding is limited to 256 KiB. Supply `media_type` for a value that
will enter a notebook export. Export production rejects a `BlobAsset` whose
media type is absent or invalid.

## `plan()`

```python
plan(
    source: str | os.PathLike[str],
    *,
    spec: ExportSpec,
    repository: ExportRepository | None = None,
    timeout: float = 30.0,
    progress: Callable[[ProgressEvent], None] | None = None,
) -> ExportPlan
```

`plan()` returns the exact state relation and reports which state fingerprints
are reusable or missing. An exact prepared export avoids notebook startup.
Otherwise, planning runs the notebook's initial autorun to inspect its baseline
and dependencies.

`timeout` must be a positive finite number. It bounds managed server startup and
transport inactivity. A caller-supplied repository stays caller-owned. A
repository opened by `plan()` closes before the call returns.

### `ExportPlan`

`ExportPlan` is an immutable record:

| Field                  | Meaning                                                 |
| ---------------------- | ------------------------------------------------------- |
| `identity`             | SHA-256 over producer, output-plan, and spec identities |
| `document_sha256`      | Authored notebook document identity                     |
| `producer_sha256`      | Notebook plus producer environment identity             |
| `output_plan_sha256`   | Identity of the authored output declarations            |
| `spec_sha256`          | Identity of the complete authored `ExportSpec`          |
| `default_alias`        | Authored default state name                             |
| `default_fingerprint`  | Complete input vector selected by the default alias     |
| `inputs`               | Sorted inferred input names                             |
| `states`               | Normalized `PlannedState` records                       |
| `outputs`              | Ordered published output names                          |
| `reusable_states`      | Reusable state fingerprints                             |
| `missing_states`       | State fingerprints that need preparation                |
| `observation_revision` | Repository observation revision used by the plan        |
| `observations`         | `ObservedState` records projected to the plan inputs    |
| `exact_reuse`          | Whether one matching prepared export supplied the plan  |

Each `PlannedState` has sorted `aliases`, a complete immutable `inputs` mapping,
and its `fingerprint`. `plan.state_fingerprints` returns every normalized
fingerprint.

```python
plan.matches(notebook_export: NotebookExport) -> bool
plan.to_dict() -> dict[str, object]
export_plan_identity(
    *, producer_sha256: str, output_plan_sha256: str, spec_sha256: str
) -> str
output_plan_sha256(spec: ExportSpec) -> str
```

`matches()` compares the spec, default, notebook, input and output names,
aliases, fingerprints, and complete input vectors. The identity helpers live in
`marimo_export.planning`.

## `prepare()`

```python
prepare(
    source: str | os.PathLike[str],
    *,
    spec: ExportSpec,
    repository: ExportRepository | None = None,
    timeout: float = 30.0,
    progress: Callable[[ProgressEvent], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> PreparedExport
```

`prepare()` returns a leased immutable repository generation. Exact reuse
returns before starting a notebook. Missing work starts one owned notebook
session, prepares every missing state, commits the complete generation, and
closes the owned process tree.

Use the returned handle as a context manager:

```python
with prepare("report.py", spec=spec) as prepared:
    notebook_export = prepared.open()
    result = prepared.write("dist/report", replace=True)
```

When `repository` is absent, `PreparedExport` owns the repository until the
handle closes. A supplied repository stays caller-owned. `cancelled` is checked
between bounded preparation phases and while waiting for a preparation
reservation. Cancellation raises an execution error with code
`preparation_cancelled` and preserves the previous committed generation.

## `build()`

```python
build(
    notebook: str | os.PathLike[str],
    *,
    spec: ExportSpec,
    output: str | os.PathLike[str],
    repository: ExportRepository | None = None,
    timeout: float = 30.0,
    replace: bool = False,
    progress: Callable[[ProgressEvent], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ExportResult
```

`build()` preflights `output`, calls `prepare()`, writes and verifies the
notebook export, then closes the prepared handle. Preflight happens before
notebook execution. An existing destination raises `NotebookExportError` with
code `destination_exists` unless `replace=True`.

Replacement uses a staged sibling directory and rollback. A successful commit
can return a warning when parent-directory synchronization or cleanup of the
retired destination fails.

## `PreparedExport`

`prepare()`, `capture()`, and `ExportRepository.prepared()` return
`PreparedExport`. Callers cannot construct it directly.

Properties:

```python
prepared.identity: str
prepared.plan: ExportPlan
prepared.path: Path
prepared.reused: bool
prepared.prepared_states: tuple[str, ...]
prepared.reused_states: tuple[str, ...]
prepared.cache_activity: CacheActivity
```

`identity` is the SHA-256 of the export generation's exact canonical
`index.json`. `reused` means the complete prepared export was reused.
`reused_states` can also contain state-level reuse during a preparation that was
not an exact export reuse.

Methods:

```python
prepared.open() -> NotebookExport
prepared.asset(relative: str) -> PreparedAsset
prepared.manifest(
    export_url: str,
    *,
    state: str | Mapping[str, object] | None = None,
    refresh_interval_ms: int | None = None,
) -> dict[str, object]
prepared.to_dict() -> dict[str, object]
prepared.write(
    output: str | os.PathLike[str],
    *,
    replace: bool = False,
    progress: Callable[[ProgressEvent], None] | None = None,
) -> ExportResult
prepared.renew() -> None
prepared.close() -> None
```

`open()`, `path`, `asset()`, `renew()`, and `write()` verify the required
repository files while the lease is alive. Keep the `PreparedExport` open while
using a `NotebookExport` returned by `open()`.

`PreparedAsset` owns an independent lease and exposes `path`, `size`,
`read_bytes()`, and idempotent `close()`. It can outlive its parent
`PreparedExport`. Close it after the response or file consumer finishes.

Calls that need files raise `RepositoryError` after close or lease loss.
Integrity changes raise `IntegrityError`. `close()` is idempotent.

[Delivery and publications](delivery-and-publications.md) defines prepared
manifests and application-level retention.

## Progress callbacks

`progress` receives ordered immutable `ProgressEvent` values synchronously:

```text
inspection_started
plan_ready
prepared_reused
state_started
state_finished
prepared_committed
write_finished
```

Each event contains `kind` and optional `completed`, `total`, `state`, `cache`,
`elapsed_seconds`, and `message` fields. `to_dict()` includes every field and
uses `None` when a field does not apply.

`ProgressKind` is the type alias for the seven supported `kind` strings.

```python
ProgressEvent(
    kind: ProgressKind,
    completed: int | None = None,
    total: int | None = None,
    state: str | None = None,
    cache: CacheActivity | None = None,
    elapsed_seconds: float | None = None,
    message: str | None = None,
)
CacheActivity(
    authored_hits: int = 0,
    authored_misses: int = 0,
    projection_hits: int = 0,
    projection_misses: int = 0,
)
```

`CacheActivity` contains nonnegative `authored_hits`, `authored_misses`,
`projection_hits`, and `projection_misses`. `to_dict()` returns those four
fields. Exceptions raised by the callback propagate to the producer call.

Progress callbacks are notifications, not transaction guards. A
`state_finished` event follows the prepared-state commit. A
`prepared_committed` event follows the generation commit. A `write_finished`
event follows destination commit and verification. An exception from one of
those callbacks leaves the preceding durable state available even though the
producer call raises. Use `StagedDelivery.commit(guard=...)` when an application
needs a check immediately before an outer directory commit.

## `ExportResult` and warnings

`build()` and `PreparedExport.write()` return immutable `ExportResult` records:

| Field             | Type or meaning                                |
| ----------------- | ---------------------------------------------- |
| `path`            | Absolute committed destination                 |
| `identity`        | SHA-256 of exact canonical `index.json` bytes  |
| `plan`            | Resolved `ExportPlan`                          |
| `reused`          | Exact prepared-export reuse                    |
| `prepared_states` | Fingerprints prepared by this operation        |
| `reused_states`   | Fingerprints reused by this operation          |
| `cache_activity`  | `CacheActivity` observed for work that ran     |
| `assets`          | Unique asset count                             |
| `asset_bytes`     | Bytes across unique assets                     |
| `index_bytes`     | Canonical index byte count                     |
| `verification`    | `VerificationResult` for the committed export  |
| `warnings`        | Recoverable post-commit `ExportWarning` values |
| `elapsed_seconds` | Write duration                                 |

`result.to_dict()` returns the same nested shape used by CLI JSON output.

An `ExportWarning` contains `code`, `message`, immutable portable `details`, and
`to_dict()`. Current warning codes are `export_parent_sync_failed` and
`retired_destination_cleanup_failed`. The destination is already visible when
either warning is returned.

## Narrow protocol records

`marimo_export.limits.CaptureLimits`, `CacheSummary`, `StateRunTimings`, and
`PhaseTimings` are exported record types used by lower-level producer protocol
integration. The high-level `plan()`, `prepare()`, `capture()`, and `build()`
calls apply package limits and have no `limits` parameter. Application code
should use the high-level calls unless it implements that lower-level protocol.

`CaptureLimits()` defaults to 64 MiB per asset and 512 MiB for the complete
asset closure. `CacheSummary` records `hits` and `misses`. `StateRunTimings`
records state count and setup, dependency execution, UI update, output
materialization, and cleanup seconds. `PhaseTimings` combines state-run timings
with producer lifecycle timings.

Use [Read and verify exports](reader.md) after writing the notebook export, or
[Sessions and inspection](sessions-and-inspection.md) to prepare from a running
session.
