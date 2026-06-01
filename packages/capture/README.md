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
expression in the active scenario. `source.cell` selects a marimo cell output
by cell name, id, or index.

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

Only that synthetic cell is scheduled by the outer script runner. Scenario
execution is delegated to `mox.evaluate`, which applies overrides before dirty
downstream notebook cells run.

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
    inputs:
      chart_width: 1200
  - id: selected_names
    inputs:
      symbols: ["AAPL", "AMZN", "MSFT"]
    ui:
      symbols_selector:
        value: ["AAPL", "MSFT"]

values:
  prices:
    source: { def: df }
    formats: [arrow, parquet]

  change_desc:
    source: { cell: change_desc, output: scenario }
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

Scenarios are finite runtime states. `inputs` override notebook definitions.
`ui` and `widgets` patch materialized objects after their producer cells run.
`source.report` captures selected display cells as an ordered report snapshot.
`on_error: record` stores display failures as artifact diagnostics, so the
remaining cells can still be exported.

Manifest `state` stores those sections as JSON for lookup. If a scenario uses a
code-authored input value, `declared_state` stores the authored expression for
provenance.

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

The archive contains the same canonical `index.json`, `bundles/...`,
`traces/...`, and `blobs/sha256/...` tree that `export` writes to disk.

## Query API

```python
import moexport as mox

export = mox.open_export("notebooks/__marimo__/static-export")

export.catalog()
export.notebooks()
export.scenarios(state={"inputs.chart_width": 1200})
export.values(value="prices")
export.formats(value="prices")
export.artifacts(
    state={"inputs.chart_width": 1200},
    value="comparison_chart",
    format="vegalite",
)
export.entries(value="summary", format="json", include_content=True)
export.files(value="prices", format="arrow")
export.notebooks()[0]["source_sha256"]
```

`export.notebook_source(...)["text"]` returns notebook text only when the spec
sets `provenance: {source: source}`.

For one bundle:

```python
bundle = export.bundle("sha256-970")

bundle.summary()
bundle.map()
bundle.artifacts(value="prices")
bundle.files(value="prices", dedupe=True)
bundle.graph("default")
```

The query API is intentionally loader-free. It exposes the semantic map,
manifest metadata, source provenance, artifact records, blob paths, and
invocation graph traces so callers can decide how to inspect the files.

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
