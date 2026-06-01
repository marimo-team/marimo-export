# moexport

Python capture runtime for marimo static export bundles.

`moexport` is the only package in this workspace that sees live Python objects.
It evaluates configured notebook expressions, applies exporters, writes
content-addressed blob files, records provenance, and produces bundle manifests
that web packages can read without Python.

## Export From A Running Notebook

```python
import moexport as mox

spec = {
    "scenarios": [{"id": "default"}],
    "values": {
        "prices": {
            "source": {"def": "df"},
            "formats": ["arrow"],
        }
    },
}

result = await mox.export(spec, bundle="notebooks/__marimo__/static-export")
```

`source.def` names a notebook definition. `source.expr` evaluates a Python
expression in the active scenario. `source.cell` selects a marimo cell by name,
id, or index and captures the output after scenario state has been applied.

## Export A Notebook File

Use `export_notebook` when the export should be initiated outside the notebook
kernel:

```python
import moexport as mox

result = mox.export_notebook(
    "notebooks/finance.py",
    spec,
    bundle="notebooks/__marimo__/static-export",
    run={"args": ["--symbol", "AAPL"]},
)
```

`export_notebook` reuses marimo's notebook resolver, so local paths and the
remote references supported by `marimo run <notebook>` go through the same
resolution path. It appends one hidden cell equivalent to:

```python
import moexport as __moexport

__moexport_result_abcd = await __moexport.export(
    __moexport_spec_abcd,
    bundle=__moexport_bundle_abcd,
)
```

Only that synthetic cell is scheduled by the outer script runner.
`mox.evaluate` applies scenario state before dirty downstream notebook cells
run.

## Capture From A Live Server

`moexport.live_capture` provides the Python HTTP helpers used by local capture
scripts. It resolves a running marimo session, installs `moexport` into that
kernel when needed, and executes scratchpad code that calls `mox.export(...)`.

```python
from moexport.live_capture import ensure_runtime, execute_scratchpad, resolve_session

session = resolve_session(
    server="http://localhost:8787",
    notebook="finance.py",
    session_id=None,
    token=None,
)
ensure_runtime(
    server="http://localhost:8787",
    session_id=session["sessionId"],
    package="moexport[all]",
    force=False,
    token=None,
)
result = execute_scratchpad(
    "http://localhost:8787",
    session["sessionId"],
    "import moexport as mox\nprint(mox.runtime().notebook.name)",
    token=None,
    timeout=30,
)
```

## CLI

Inspect a notebook before writing a spec:

```bash
marimo-export inspect defs notebooks/finance.py
marimo-export inspect source notebooks/finance.py
```

Capture a bundle from JSON or YAML:

```bash
marimo-export notebook notebooks/finance.py \
  --spec notebooks/export-specs/yaml/finance--dashboard.yaml \
  --bundle notebooks/__marimo__/static-export
```

Query the result:

```bash
marimo-export query notebooks/__marimo__/static-export
marimo-export query notebooks/__marimo__/static-export scenarios
marimo-export query notebooks/__marimo__/static-export entries \
  --value summary \
  --format json \
  --content
```

## Spec Shape

```yaml
scenarios:
  - id: default
  - id: wide_chart
    state:
      chart_width: 1200
  - id: selected_names
    state:
      symbols: ["AAPL", "AMZN", "MSFT"]
      symbols_selector.value: ["AAPL", "MSFT"]

values:
  prices:
    source: { def: df }
    formats: [arrow, parquet]

  change_desc:
    source: { cell: change_desc }
    formats:
      html:
        export:
          type: ref
          ref: moexport.exporters.core:html
        options:
          filename: change-desc.html
          format: marimo.cell_output.html.v1

  metrics_report:
    source:
      report:
        cells:
          - name: summary
            label: Summary
            order: 0
          - name: chart
            label: Chart
            order: 1
        include_source: false
        on_error: record
    formats: [display, markdown]
```

Scenarios are finite runtime states. Bare `state` keys override notebook
definitions. Dotted `state` keys patch materialized objects after their
producer cells run. For example, `symbols_selector.value` patches the
`value` attribute on `symbols_selector`.

`source.report` captures selected display cells as an ordered report snapshot.
`on_error: record` stores display failures as artifact diagnostics, so the
remaining cells can still be exported.

Manifest `state` stores the resolved JSON state used for lookup. If a scenario
uses a code-authored state value, `declared_state` stores the authored
expression for provenance. The evaluated value must be JSON-compatible, so the
manifest does not use Python `repr(...)` as identity.

Build sweeps by generating a finite scenarios list before capture. `mox.export`
consumes the resolved list from YAML or JSON.

## Exporters

Built-in formats compile to exporter callables:

| Format         | Exporter                                  |
| -------------- | ----------------------------------------- |
| `json`         | `moexport.exporters.core:json`            |
| `text`         | `moexport.exporters.core:text`            |
| `html`         | `moexport.exporters.core:html`            |
| `arrow`        | `moexport.exporters.dataframe:arrow`      |
| `parquet`      | `moexport.exporters.dataframe:parquet`    |
| `vegalite`     | `moexport.exporters.altair:vegalite`      |
| `png`          | `moexport.exporters.altair:png`           |
| `anywidget`    | `moexport.exporters.anywidget:bundle`     |
| `display`      | `moexport.exporters.display:display_json` |
| `display_json` | `moexport.exporters.display:display_json` |
| `markdown`     | `moexport.exporters.display:markdown`     |

Use an explicit exporter config when a value needs custom projection code. A
referenced exporter uses `module:function` import syntax:

```yaml
export:
  type: ref
  ref: moexport.exporters.dataframe:arrow
```

Inline exporter code defines a callable named `export`:

```yaml
export:
  type: code
  code: |
    import json


    def export(value, ctx, **options):
        blob = ctx.write_blob(
            "summary.json",
            json.dumps(value, allow_nan=False, indent=2).encode("utf-8"),
            media_type="application/json",
        )
        return ctx.artifact(
            format="summary.json.v1",
            media_type="application/json",
            files={"data": blob},
            entry="data",
            metadata={"kind": "summary"},
        )
```

An exporter receives the live Python value, an exporter context, and optional
format options. The context writes blob files and returns artifact envelopes in
the manifest. Built-in exporters live under `moexport.exporters`:

- `core:json`
- `core:text`
- `core:html`
- `dataframe:arrow`
- `dataframe:parquet`
- `altair:vegalite`
- `altair:png`
- `anywidget:bundle`
- `display:display_json`
- `display:markdown`

## Archive Transport

`archive_bundle` turns an existing export result into zip bytes:

```python
result = await mox.export(spec)
archive = mox.archive_bundle(result)
```

The archive contains the same canonical `index.json`, `bundles/...`, and
`blobs/sha256/...` tree that `export` writes to disk. Invocation traces live
under each bundle at `bundles/<id>/traces/...`.

## Bundle Contract

Every artifact payload is stored as a content-addressed blob. `BlobRef.href`
is bundle-relative, `BlobRef.size` records the byte length, and
`BlobRef.sha256` records the digest readers verify before parsing loaded
artifact bytes.

`ArtifactRecord.format_id` identifies the artifact payload format that readers
and loaders match, for example `dataframe.arrow.v1`. Exporters can write
multiple named files for one artifact, but `data.entry` must point at one of
those files when the format has a canonical entry.

## Query API

```python
import moexport as mox

export = mox.open_export("notebooks/__marimo__/static-export")

export.catalog()
export.notebooks()
export.scenarios(state={"chart_width": 1200})
export.values(value="prices")
export.formats(value="prices")
export.artifacts(
    state={"chart_width": 1200},
    value="comparison_chart",
    format="vegalite",
)
export.entries(value="summary", format="json", include_content=True)
export.files(value="prices", format="arrow")
export.notebooks()[0]["source_sha256"]
```

`export.notebook_source(...)["text"]` returns notebook text only when the spec
sets `provenance: { source: "source" }`.

For one bundle:

```python
bundle = export.bundle("sha256-970")

bundle.summary()
bundle.map()
bundle.artifacts(value="prices")
bundle.files(value="prices", dedupe=True)
bundle.graph("default")
```

Query objects do not load artifact payloads. They expose manifest metadata,
source provenance, artifact records, blob paths, and invocation graph traces so
callers can inspect the stored files with their own loaders.

Common selectors:

- `bundle`: Bundle id or id prefix. Omit it to query all bundles from an export
  root.
- `scenario`: Scenario id.
- `state`: Resolved-state filter. Dotted keys match object patches such as
  `symbols_selector.value`.
- `value`: Exported value name.
- `format`: Spec format key such as `arrow` or `summary`.
- `format_id`: Artifact payload format such as `dataframe.arrow.v1`.
- `media_type`: Artifact media type.

### `open_export(path)`

Opens a static export root, bundle directory, or manifest file.

Returns an `ExportQuery`. Raises when the path cannot resolve to an export
root.

### `export.catalog()`

Returns a compact root index with counts, bundle summaries, notebook records,
value records, format records, state keys, media types, and scenario rows.

### `export.bundle(id=None)`

Opens one bundle as a `BundleQuery`.

- `id`: Bundle id or id prefix. Omit it only when the export root contains one
  bundle.

Raises when no bundle exists, when the prefix matches no bundle, or when the
prefix is ambiguous.

### `export.scenarios(...)`

Returns scenario rows across bundles.

Rows include the bundle id, bundle path, notebook record, scenario id, resolved
state, available value and format matrix, and artifact count.

### `export.values(...)` and `export.formats(...)`

`values(...)` returns exported value specs across bundles. `formats(...)`
returns format availability grouped by value and format name after applying the
same scenario, state, value, and format selectors used by `artifacts(...)`.

### `export.artifacts(...)` and `export.artifact(...)`

`artifacts(...)` returns flattened artifact rows with scenario, state, value,
source, format key, `format_id`, media type, files, and metadata.

`artifact(...)` returns exactly one artifact row. It raises when the selectors
match no artifacts or more than one artifact.

### `export.files(...)` and `export.file(...)`

`files(...)` returns blob file rows with semantic usage metadata. It deduplicates
rows by href by default across an export root.

`file(...)` returns exactly one file row. It raises when the selectors match no
files or more than one file.

### `export.entries(...)` and `export.entry(...)`

`entries(...)` returns the canonical entry file for each matching artifact.

- `include_content`: Inline small JSON or text content. Defaults to `False`.
- `max_bytes`: Maximum byte size to inline when `include_content` is enabled.
  Defaults to `65536`.

`entry(...)` returns exactly one entry row. It raises when the selectors match
no entries or more than one entry.

### `export.notebooks()` and `export.notebook_source(...)`

`notebooks()` returns notebook records grouped across bundles.

`notebook_source(...)` returns exactly one stored notebook source and includes
the source text. It raises when no source was stored or when the selectors match
more than one source.

### `bundle.summary()`, `bundle.map()`, `bundle.trace()`, and `bundle.graph()`

`summary()` returns bundle-level identity and count metadata. `map()` expands
the same bundle into values, scenarios, artifacts, files, and traces. `trace()`
loads the latest invocation trace or one scenario trace. `graph()` returns the
captured dependency graph metadata for one scenario or all scenarios.

## Mechanics

- `spec.py` validates scenarios, expressions, formats, exporter references, and
  inline exporter code.
- `evaluate.py` reuses live globals and reruns only notebook cells made dirty by
  scenario state.
- `exporters/` turns Python values into portable artifact records.
- `bundle.py` writes `manifest.json`, shared `blobs/sha256/...` files,
  notebook source provenance, invocation traces, and root `index.json`.
- `archive.py` zips a canonical export root for in-memory or network transport.
- `notebook.py` resolves notebook references through marimo, injects one hidden
  export cell, and runs it through marimo's script context.
- `cli/` exposes `marimo-export`.
- `query/` provides structured listing primitives over finished export roots
  and bundle directories.

The output is read-only. Browser packages never execute Python.
