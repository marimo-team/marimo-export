# @marimo-team/export-loader-vegalite

Loader for `vegalite.v1` formats.

This package turns exported Vega-Lite JSON into a browser handle that can return
the raw spec or render an interactive chart with `vega-embed`.

```ts
import { vegaliteLoader } from "@marimo-team/export-loader-vegalite";
import { readLatestExport } from "@marimo-team/export-reader";

const exp = await readLatestExport({
  root: "/export/",
  loaders: [vegaliteLoader()],
});

const chart = await exp
  .get({ scenario: "default", value: "comparison_chart", format: "vegalite" })
  .load();

await chart.render(document.querySelector("#chart")!);

const spec = await chart.spec();
```

Mechanics:

- Supports `vegalite.v1`.
- Reads the format entry file as JSON.
- Renders through `vega-embed`.
- Exposes `.spec()` for callers that need the language-agnostic payload.
