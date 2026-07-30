# Vega-Lite loader

Load and mount an exported Vega-Lite chart:

```ts
import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";

const chart = await output.load(vegaLiteLoader({ actions: false }));
const mounted = await chart.mount(host, { renderer: "svg" });

await mounted.dispose();
```

Install `vega-embed` beside `@marimo-team/marimo-export`. Review charts that
load external data or images under the same browser policy as application code.
