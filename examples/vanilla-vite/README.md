# Vanilla Vite export reader

Browser-only SPA over a marimo static export bundle.

The checked-in bundle under `public/export/` was generated with
`marimo-export` from `notebooks/finance.py` and the finance dashboard spec:

```bash
uv run marimo-export notebook notebooks/finance.py \
  --spec notebooks/export-specs/json/finance--dashboard.json \
  --to examples/vanilla-vite/public/export
```

The YAML spec is equivalent:

```bash
uv run marimo-export notebook notebooks/finance.py \
  --spec notebooks/export-specs/yaml/finance--dashboard.yaml \
  --to examples/vanilla-vite/public/export
```

## Run

```bash
pnpm --filter @marimo-team/export-example-vanilla dev
```

## Bundle Contents

The app calls `readExport({ root: "/export/" })`, reads `public/export/index.json`,
opens the latest manifest, and loads:

- `summary/json`: custom code-defined JSON exporter.
- `symbols_selector/json`: custom code-defined JSON exporter for marimo
  multiselect state.
- `prices/arrow`: Arrow dataframe loaded by
  `@marimo-team/export-loader-arrow`.
- `prices/parquet`: Parquet dataframe loaded by
  `@marimo-team/export-loader-parquet`.
- `change_desc/html`: custom HTML exporter over the typed source
  `{ cell: change_desc }`.
- `comparison_chart/vegalite`: Vega-Lite format loaded by
  `@marimo-team/export-loader-vegalite`.
- `comparison_chart/png_nogrid`: custom code-defined PNG exporter.
- `ohlc_dashboard/bundle`: AnyWidget bundle hydrated by
  `@marimo-team/export-loader-anywidget`.

Scenario `state` keys override notebook definitions. Dotted state keys such as
`symbols_selector.value` patch object attributes before the SPA switches between
precomputed scenarios.
