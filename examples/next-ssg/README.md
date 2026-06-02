# Next.js SSG example

Next.js example that renders static pages from marimo export bundles.

The default build reads the checked-in bundle at `public/export/finance`. A
running marimo server is only needed when regenerating that public bundle or when
building the optional archive-backed routes.

## Build

```bash
pnpm --filter @marimo-team/export-example-next-ssg build
```

The generated site is written to `examples/next-ssg/out`.

## Regenerate The Public Bundle

Start marimo from the repository root:

```bash
uv run marimo edit notebooks/finance.py \
  --port 8483 \
  --no-token \
  --no-skew-protection
```

Build with capture enabled:

```bash
MARIMO_CAPTURE=force pnpm --filter @marimo-team/export-example-next-ssg build
```

The example reads connection settings from `.env.local`:

```bash
MARIMO_SERVER_URL=http://localhost:8483
MARIMO_SERVER_TOKEN=
MARIMO_NOTEBOOK=notebooks/finance.py
MARIMO_CAPTURE=0
MARIMO_ARCHIVE_CAPTURE=0
```

Set `MARIMO_ARCHIVE_CAPTURE=1` to also render
`market-window/[start]/[end]` routes by capturing in-memory bundles during
`next build`.

## Mechanics

- `src/lib/spec.ts` defines the public-bundle export spec. It captures summary
  JSON, a named marimo cell output, a PNG chart, a Vega-Lite chart, and an
  AnyWidget dashboard.
- `src/lib/local-export.ts` captures the bundle when requested, then reads it
  with `@marimo-team/export-reader` through a file-backed fetch during SSG.
- `src/app/compare/[pair]/page.tsx` statically generates comparison pages from
  the public bundle.
- `src/app/market-window/[start]/[end]/page.tsx` is optional. When
  `MARIMO_ARCHIVE_CAPTURE=1`, it captures an archive with
  `client.archive`, opens the returned zip bytes with `readExport(...)`,
  and renders JSON, Arrow, and PNG formats
  without writing a public bundle folder.
- `src/components/ohlc-widget-panel.tsx` hydrates the AnyWidget format and
  bridges its model to React state through `createWidgetStore`.
- `src/components/vega-lite-chart.tsx` loads the static bundle in the browser
  and renders the Vega-Lite format.
