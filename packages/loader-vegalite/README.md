# Vega-Lite loader workspace

This private workspace package owns versioned Vega-Lite validation, mounting,
and view disposal through `vega-embed`.

Consumers install `@marimo-team/marimo-export` with `vega-embed`, then import
the public loader subpath:

```ts
import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";

const chart = await output.load(vegaLiteLoader({ actions: false }));
const mounted = await chart.mount(host, { renderer: "svg" });

console.log(chart.spec);
await mounted.dispose();
```

The media type carries the Vega-Lite schema major. The loader freezes the
decoded specification and clones it for each mount. Disposal finalizes the
Vega view and removes owned DOM.

Vega specifications can request external data and images. Apply the same
origin and Content Security Policy rules used for application-authored Vega.
