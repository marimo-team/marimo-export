# moexport

Python capture runtime for marimo static export bundles.

`moexport` evaluates configured notebook sources, applies exporters, writes
content-addressed blobs, records provenance, and produces manifests that browser
packages can read without Python.

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

result = await mox.capture(spec, to="notebooks/__marimo__/static-export")
```

`source.def` names a notebook definition. `source.expr` evaluates a Python
expression in the active scenario. `source.cell` selects a marimo cell by name,
id, or index and captures its output after scenario state has been applied.

## Export A Notebook File

Use `capture_notebook` when capture starts outside the notebook kernel:

```python
import moexport as mox

result = mox.capture_notebook(
    "notebooks/finance.py",
    spec,
    to="notebooks/__marimo__/static-export",
    run={"args": ["--symbol", "AAPL"]},
)
```

`capture_notebook` resolves local paths and remote references through the same
path marimo uses for `marimo run <notebook>`. It injects one hidden export cell:

```python
import moexport as __moexport

__moexport_result_abcd = await __moexport.capture(
    __moexport_spec_abcd,
    to=__moexport_output_abcd,
)
```

Only that synthetic cell is scheduled by the outer script runner.
`mox.evaluate` applies scenario state before dirty downstream notebook cells run.

## Capture From A Live Server

`CaptureClient` is a small Python client for a running marimo server:

```python
from moexport import CaptureClient, RuntimeInstall

capture = CaptureClient(
    "http://localhost:8787",
    notebook="finance.py",
    runtime=RuntimeInstall("moexport[all]"),
)

result = capture.capture(spec, to="examples/vanilla-vite/public/export")
archive = capture.archive(spec)
```

The default runtime is `"preinstalled"`, which checks that `moexport` is already
importable in the target kernel. Pass `RuntimeInstall(...)` when the caller owns
the package source and wants the client to install it.

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
  --to notebooks/__marimo__/static-export
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
          format_id: marimo.cell_output.html.v1

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

Scenarios are finite runtime states. `state` overrides notebook definitions.
Dotted `state` keys update object attributes after producer cells run. For
example, `symbols_selector.value` sets the `value` attribute on
`symbols_selector`.

`source.report` captures selected display cells as an ordered report snapshot.
`on_error: record` stores display failures as format diagnostics so the
remaining cells can still be exported.

Manifest `state` stores the resolved JSON state used for lookup. If a scenario
uses a code-authored state value, `declared_state` stores the authored expression
for provenance. The evaluated value must be JSON-compatible.

## Formats And Exporters

Built-in format names compile to exporter callables:

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

Use an explicit exporter config when a value needs custom projection code:

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
            format_id="summary.json.v1",
            media_type="application/json",
            files={"data": blob},
            entry="data",
            metadata={"kind": "summary"},
        )
```

An exporter receives the live Python value, an `ExporterContext`, and optional
format options. The context exposes `scenario_id`, `value_name`, and
`format_name`. `ctx.write_blob(...)` writes a content-addressed file.
`ctx.artifact(...)` returns the manifest record for the format.

## Archive Transport

`archive_bundle` turns an existing `CaptureResult` into zip bytes:

```python
result = await mox.capture(spec)
archive = mox.archive_bundle(result)
```

The archive contains the same `index.json`, `bundles/...`, and
`blobs/sha256/...` tree that `capture` writes to disk. Invocation traces live
under each bundle at `bundles/<id>/traces/...`.

## Bundle Contract

Every exported format payload is stored as a content-addressed blob.
`BlobRef.href` is bundle-relative, `BlobRef.size` records the byte length, and
`BlobRef.sha256` records the digest readers verify before parsing bytes.

`ArtifactRecord.format_id` identifies the portable payload format that readers
and loaders match, for example `dataframe.arrow.v1`. Exporters can write
multiple named files for one format. `data.entry` points at the canonical file
when the format has one.

## Query API

```python
import moexport as mox

export = mox.open_export("notebooks/__marimo__/static-export")

export.catalog()
export.notebooks()
export.scenarios(state={"chart_width": 1200})
export.values(value="prices")
export.format_catalog(value="prices")
export.formats(
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
bundle.formats(value="prices")
bundle.files(value="prices", dedupe=True)
bundle.graph("default")
```

Query objects do not load format payloads. They expose manifest metadata,
source provenance, format records, blob paths, and invocation graph traces so
callers can inspect the stored files with their own loaders.

Common selectors:

- `bundle`: Bundle id or id prefix. Omit it to query all bundles from an export
  root.
- `scenario`: Scenario id.
- `state`: Resolved-state filter. Dotted keys match patched object attributes
  such as `symbols_selector.value`.
- `value`: Exported value name.
- `format`: Authored format name such as `arrow` or `summary`.
- `format_id`: Portable payload format such as `dataframe.arrow.v1`.
- `media_type`: Format media type.

### `open_export(path)`

Opens a static export root, bundle directory, or manifest file.

Returns an `ExportQuery`. Raises when the path cannot resolve to an export root.

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
state, available value and format matrix, and format count.

### `export.values(...)` and `export.format_catalog(...)`

`values(...)` returns exported value specs across bundles.
`format_catalog(...)` returns format availability grouped by value and authored
format name after applying the same scenario, state, value, and format selectors
used by `formats(...)`.

### `export.formats(...)` and `export.format(...)`

`formats(...)` returns flattened format rows with scenario, state, value,
source, authored format name, `format_id`, media type, files, and metadata.

`format(...)` returns exactly one format row. It raises when the selectors match
no formats or more than one format.

### `export.files(...)` and `export.file(...)`

`files(...)` returns blob file rows with semantic usage records. It deduplicates
rows by href by default across an export root.

`file(...)` returns exactly one file row. It raises when the selectors match no
files or more than one file.

### `export.entries(...)` and `export.entry(...)`

`entries(...)` returns the canonical entry file for each matching format.

- `include_content`: Inline small JSON or text content. Defaults to `False`.
- `max_bytes`: Maximum byte size to inline when `include_content` is enabled.
  Defaults to `65536`.

`entry(...)` returns exactly one entry row. It raises when the selectors match no
entries or more than one entry.

### `export.notebooks()` and `export.notebook_source(...)`

`notebooks()` returns notebook records grouped across bundles.

`notebook_source(...)` returns exactly one stored notebook source and includes
the source text. It raises when no source was stored or when the selectors match
more than one source.

### `bundle.summary()`, `bundle.map()`, `bundle.trace()`, and `bundle.graph()`

`summary()` returns bundle-level identity and count metadata. `map()` expands the
same bundle into values, scenarios, formats, files, and traces. `trace()` loads
the latest invocation trace or one scenario trace. `graph()` returns captured
dependency graph metadata for one scenario or all scenarios.
