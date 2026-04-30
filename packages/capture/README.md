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
    "scenarios": [{"id": "default", "state": {}}],
    "values": {
        "prices": {
            "source": "df",
            "formats": {
                "arrow": {
                    "export": {
                        "type": "ref",
                        "ref": "moexport.exporters.dataframe:arrow",
                    }
                }
            },
        }
    },
}

result = await mox.export(spec, bundle="notebooks/__marimo__/static-export")
```

`source` is a Python expression evaluated in the notebook runtime. It can be a
def such as `df`, a derived expression such as `df.head()`, or a marimo runtime
selector such as `mox.runtime().cell("change_desc").output`.

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
      symbols_selector.value: ["AAPL", "MSFT"]

values:
  prices:
    source: df
    formats:
      arrow:
        export:
          type: ref
          ref: moexport.exporters.dataframe:arrow
      parquet:
        export:
          type: ref
          ref: moexport.exporters.dataframe:parquet

  change_desc:
    source: mox.runtime().cell("change_desc").output
    formats:
      html:
        export:
          type: ref
          ref: moexport.exporters.core:html
        options:
          filename: change-desc.html
          format: marimo.cell_output.html.v1
```

Scenarios are finite runtime states. A state key can override a notebook def
such as `chart_width`, or patch an object path such as
`symbols_selector.value`.

`state` in the manifest is the resolved JSON state used for lookup. If a spec
uses code-authored state, `declared_state` stores the authored expression for
provenance.

## Exporters

Exporters are ordinary Python callables. A referenced exporter uses
`module:function` import syntax:

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
export.notebook_source(state={"chart_width": 1200})["text"]
```

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
- `bundle.py` writes `manifest.json`, shared `blobs/sha256/...` files, notebook
  source blobs, invocation traces, and root `index.json`.
- `archive.py` zips a canonical export root for in-memory or network transport.
- `notebook.py` resolves notebook references through marimo, injects one hidden
  export cell, and runs it through marimo's script context.
- `cli/` exposes `marimo-export`.
- `query/` provides structured listing primitives over finished export roots
  and bundle directories.

The output is read-only. Browser packages never execute Python.
